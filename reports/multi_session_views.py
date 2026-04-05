from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from athleteai.permissions import BlockSuperUserPermission
from reports.annotation_utils import (
    aggregate_stats_rows,
    build_dated_filename,
    build_workbook_bytes,
    normalize_athlete_profile,
    normalize_errors,
)
from reports.models import (
    AnnotationMatchResult,
    AnnotationSession,
    AthleteReport,
    MultiVideoSession,
    MultiVideoSessionItem,
    VideoUrl,
)
from reports.multi_session_serializers import MultiVideoSessionSerializer
from users.credit_service import CreditCommitError, commit_credit, reserve_credit
from users.models import CustomUser
from utils.excel_to_pdf import count_matches, process_excel_file
from utils.helpers import get_file_hash
from utils.s3_service import S3Service


def _get_session_or_404(session_id, user):
    return MultiVideoSession.objects.filter(id=session_id, created_by=user).first()


def _get_item_or_404(session, item_id):
    return session.items.filter(id=item_id, is_removed=False).first()


class MultiVideoSessionListCreateView(APIView):
    """
    GET  → list all multi-video sessions for the current user
    POST → create a new multi-video session with selected video IDs
    """
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def get(self, request):
        sessions = MultiVideoSession.objects.filter(created_by=request.user).prefetch_related("items")
        serializer = MultiVideoSessionSerializer(sessions, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        video_ids = request.data.get("video_ids") or []
        title = request.data.get("title") or ""

        if not isinstance(video_ids, list) or not video_ids:
            return Response({"error": "video_ids must be a non-empty list."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate all video IDs belong to this user
        videos = VideoUrl.objects.filter(id__in=video_ids, user=request.user)
        found_ids = set(videos.values_list("id", flat=True))
        missing_ids = [vid for vid in video_ids if vid not in found_ids]
        if missing_ids:
            return Response(
                {"error": "Some video IDs are invalid or do not belong to you.", "invalid_ids": missing_ids},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check for duplicate video IDs in request
        if len(video_ids) != len(set(video_ids)):
            return Response({"error": "Duplicate video IDs are not allowed."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            session = MultiVideoSession.objects.create(
                created_by=request.user,
                title=title or None,
            )
            video_map = {v.id: v for v in videos}
            for vid_id in video_ids:
                MultiVideoSessionItem.objects.create(
                    session=session,
                    video=video_map[vid_id],
                )

        serializer = MultiVideoSessionSerializer(session)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class MultiVideoSessionDetailView(APIView):
    """
    GET   → session detail with all video items and their statuses
    """
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def get(self, request, session_id):
        session = _get_session_or_404(session_id, request.user)
        if not session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = MultiVideoSessionSerializer(session)
        return Response(serializer.data, status=status.HTTP_200_OK)


class MultiVideoSessionStartVideoView(APIView):
    """
    POST → Start annotation for a specific video item.
    Creates an AnnotationSession for that video if not already created (resume support).
    Returns annotation_session_id so frontend can use existing annotation APIs.
    """
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def post(self, request, session_id, item_id):
        session = _get_session_or_404(session_id, request.user)
        if not session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
        if session.status == MultiVideoSession.STATUS_COMPLETED:
            return Response({"error": "This session is already finalized."}, status=status.HTTP_409_CONFLICT)

        item = _get_item_or_404(session, item_id)
        if not item:
            return Response({"error": "Video item not found or has been removed."}, status=status.HTTP_404_NOT_FOUND)
        if not item.video:
            return Response({"error": "This video item no longer has an associated video."}, status=status.HTTP_400_BAD_REQUEST)

        # Resume: if annotation session already exists, just return it
        if item.annotation_session:
            if item.status == MultiVideoSessionItem.STATUS_COMPLETED:
                # Admin wants to re-edit a completed video — reopen it
                item.status = MultiVideoSessionItem.STATUS_IN_PROGRESS
                item.save(update_fields=["status", "updated_at"])
            return Response(
                {
                    "status": "resumed",
                    "message": "Annotation session already exists. Resuming.",
                    "item_id": item.id,
                    "annotation_session_id": item.annotation_session.id,
                    "video_id": item.video_id,
                    "item_status": item.status,
                },
                status=status.HTTP_200_OK,
            )

        # Create new AnnotationSession for this video
        with transaction.atomic():
            annotation_session = AnnotationSession.objects.create(
                user=request.user,
                video=item.video,
                video_url=item.video.url,
            )
            item.annotation_session = annotation_session
            item.status = MultiVideoSessionItem.STATUS_IN_PROGRESS
            item.save(update_fields=["annotation_session", "status", "updated_at"])

        return Response(
            {
                "status": "started",
                "message": "Annotation session created. Use the annotation APIs with this session ID.",
                "item_id": item.id,
                "annotation_session_id": annotation_session.id,
                "video_id": item.video_id,
                "item_status": item.status,
            },
            status=status.HTTP_201_CREATED,
        )


class MultiVideoSessionCompleteVideoView(APIView):
    """
    POST → Mark a video item as completed.
    Validates at least 1 match result exists for this video's annotation session.
    """
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def post(self, request, session_id, item_id):
        session = _get_session_or_404(session_id, request.user)
        if not session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
        if session.status == MultiVideoSession.STATUS_COMPLETED:
            return Response({"error": "This session is already finalized."}, status=status.HTTP_409_CONFLICT)

        item = _get_item_or_404(session, item_id)
        if not item:
            return Response({"error": "Video item not found or has been removed."}, status=status.HTTP_404_NOT_FOUND)
        if item.status == MultiVideoSessionItem.STATUS_PENDING:
            return Response(
                {"error": "You must start annotation for this video before marking it complete."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not item.annotation_session:
            return Response({"error": "No annotation session found for this video item."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate at least 1 match result
        match_count = item.annotation_session.match_results.count()
        if match_count == 0:
            return Response(
                {
                    "error": "Cannot complete this video — at least 1 match result is required.",
                    "message": "Add at least one match result before marking this video as complete.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        item.status = MultiVideoSessionItem.STATUS_COMPLETED
        item.save(update_fields=["status", "updated_at"])

        return Response(
            {
                "status": "success",
                "message": "Video marked as completed.",
                "item_id": item.id,
                "match_count": match_count,
            },
            status=status.HTTP_200_OK,
        )


class MultiVideoSessionRemoveVideoView(APIView):
    """
    DELETE → Soft-remove a video from the session.
    Keeps annotation data intact but excludes this video from the final report.
    """
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def delete(self, request, session_id, item_id):
        session = _get_session_or_404(session_id, request.user)
        if not session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
        if session.status == MultiVideoSession.STATUS_COMPLETED:
            return Response({"error": "Cannot remove a video from a finalized session."}, status=status.HTTP_409_CONFLICT)

        item = _get_item_or_404(session, item_id)
        if not item:
            return Response({"error": "Video item not found or already removed."}, status=status.HTTP_404_NOT_FOUND)

        # Ensure at least 1 video remains after removal
        active_count = session.items.filter(is_removed=False).count()
        if active_count <= 1:
            return Response(
                {"error": "Cannot remove the last video from a session. Delete the session instead."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        item.is_removed = True
        item.save(update_fields=["is_removed", "updated_at"])

        return Response(
            {"status": "success", "message": "Video removed from session.", "item_id": item_id},
            status=status.HTTP_200_OK,
        )


class MultiVideoSessionFinalizeView(APIView):
    """
    POST → Finalize the multi-video session.
    Collects all events and match results from all completed video items,
    renumbers matches sequentially, builds the Excel report, and assigns to a user.
    """
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def post(self, request, session_id):
        session = _get_session_or_404(session_id, request.user)
        if not session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)

        if session.status == MultiVideoSession.STATUS_COMPLETED and session.generated_report_id:
            return Response(
                {
                    "status": "already_completed",
                    "session_id": session.id,
                    "report_id": session.generated_report_id,
                    "message": "This session is already finalized.",
                },
                status=status.HTTP_200_OK,
            )

        # Get all active (non-removed) items
        active_items = list(session.items.filter(is_removed=False).order_by("created_at"))
        if not active_items:
            return Response({"error": "No videos in this session."}, status=status.HTTP_400_BAD_REQUEST)

        # All active items must be completed
        incomplete = [item.id for item in active_items if item.status != MultiVideoSessionItem.STATUS_COMPLETED]
        if incomplete:
            return Response(
                {
                    "error": "All videos must be completed before finalizing.",
                    "incomplete_item_ids": incomplete,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Collect all events and match results — renumber matches globally
        all_events_remapped = []
        all_match_results_remapped = []
        global_match_counter = 0

        for item in active_items:
            ann_session = item.annotation_session
            if not ann_session:
                return Response(
                    {"error": f"Video item {item.id} has no annotation session."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            local_match_numbers = sorted(
                ann_session.match_results.values_list("match_number", flat=True).distinct()
            )
            if not local_match_numbers:
                return Response(
                    {"error": f"Video item {item.id} has no match results. At least 1 match is required per video."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Build local → global match number mapping
            match_number_map = {}
            for local_num in local_match_numbers:
                global_match_counter += 1
                match_number_map[local_num] = global_match_counter

            # Remap events
            for event in ann_session.events.all():
                global_num = match_number_map.get(event.match_number)
                if global_num is None:
                    continue
                event.match_number = global_num
                all_events_remapped.append(event)

            # Remap match results
            for result in ann_session.match_results.all():
                global_num = match_number_map.get(result.match_number)
                if global_num is None:
                    continue

                # Create a lightweight in-memory result object with remapped match number
                class _RemappedResult:
                    pass

                remapped = _RemappedResult()
                remapped.match_number = global_num
                remapped.result = result.result
                remapped.match_type = result.match_type
                remapped.referee_decision = result.referee_decision
                remapped.disqualified = result.disqualified
                remapped.opponent = result.opponent
                all_match_results_remapped.append(remapped)

        # Build stats from remapped events
        stats_counters, match_numbers = aggregate_stats_rows(all_events_remapped)

        if not match_numbers:
            return Response(
                {
                    "error": "No valid stat events found across all videos.",
                    "message": "Add at least one non-note event with move_name, player, and outcome.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        match_results_map = {r.match_number: r for r in all_match_results_remapped}
        missing_results = [m for m in match_numbers if m not in match_results_map]
        if missing_results:
            return Response(
                {
                    "error": "Missing match results for some matches.",
                    "missing_match_numbers": missing_results,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Resolve target user (admin can assign to any user)
        is_admin = getattr(request.user, "role", None) == "admin"
        target_user = request.user
        target_user_id = request.data.get("user_id")
        if target_user_id not in (None, ""):
            if not is_admin:
                return Response(
                    {"error": "Only admins can finalize reports for other users."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            try:
                target_user = CustomUser.objects.get(id=int(target_user_id))
            except (TypeError, ValueError):
                return Response({"error": "user_id must be an integer."}, status=status.HTTP_400_BAD_REQUEST)
            except CustomUser.DoesNotExist:
                return Response({"error": "Invalid user_id provided."}, status=status.HTTP_400_BAD_REQUEST)

        athlete_profile = normalize_athlete_profile(target_user, request.data.get("athlete"))
        xlsx_bytes = build_workbook_bytes(athlete_profile, stats_counters, match_numbers, match_results_map)

        now_dt = timezone.now()
        fallback_name = athlete_profile["name"].replace(" ", "") or f"user_{target_user.id}"
        requested_filename = request.data.get("filename") or fallback_name
        filename = build_dated_filename(str(requested_filename), now_dt)

        excel_file = SimpleUploadedFile(
            name=filename,
            content=xlsx_bytes,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        try:
            excel_file.seek(0)
            match_count = count_matches(excel_file)
        except Exception:
            return Response({"error": "Failed to read generated workbook."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if match_count <= 0:
            return Response({"error": "No matches found in generated workbook."}, status=status.HTTP_400_BAD_REQUEST)

        must_check_credits = not is_admin
        ticket = None
        if must_check_credits:
            ok, ticket, msg = reserve_credit(target_user, units=match_count)
            if not ok:
                return Response(
                    {"status": "blocked", "code": "INSUFFICIENT_CREDITS", "message": msg, "match_count": match_count},
                    status=402,
                )
            if getattr(ticket, "source", None) == "one_time" and match_count < 4:
                return Response(
                    {"status": "blocked", "message": "One-time PDF requires at least 4 matches.", "match_count": match_count},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            excel_file.seek(0)
            file_hash = get_file_hash(excel_file)
        except Exception:
            return Response({"error": "Failed to hash generated workbook."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        duplicate_report = AthleteReport.objects.filter(user=target_user, file_hash=file_hash).first()
        if duplicate_report:
            return Response(
                {
                    "status": "duplicate",
                    "message": "This generated file already exists for the user.",
                    "existing_report_id": duplicate_report.id,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            excel_file.seek(0)
            processed = process_excel_file(excel_file)
        except Exception as exc:
            return Response(
                {"status": "error", "message": "Generated workbook processing failed.", "detail": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if not isinstance(processed, tuple) or len(processed) < 2:
            return Response({"status": "error", "message": "Invalid processor response."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        result, success = processed[0], processed[1]
        if not success:
            return Response(
                {"status": "error", "message": "Validation failed.", "errors": normalize_errors(result)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        s3 = S3Service()
        s3_key_uploaded = None
        s3_url_uploaded = None
        try:
            excel_file.seek(0)
            s3_result = s3.upload_files([excel_file], user_id=target_user.id)
            if not s3_result or "key" not in s3_result[0]:
                return Response({"error": "Failed to upload generated file to storage."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            s3_key_uploaded = s3_result[0]["key"]
            s3_url_uploaded = s3_result[0].get("url")
            file_size_mb = round(getattr(excel_file, "size", 0) / (1024 * 1024), 2)

            with transaction.atomic():
                report = AthleteReport.objects.create(
                    user=target_user,
                    filename=filename,
                    pdf_data=result,
                    file_size_mb=file_size_mb,
                    file_hash=file_hash,
                    s3_key=s3_key_uploaded,
                )
                if must_check_credits and ticket:
                    commit_credit(ticket)

                session.status = MultiVideoSession.STATUS_COMPLETED
                session.finalized_at = timezone.now()
                session.generated_report = report
                session.save(update_fields=["status", "finalized_at", "generated_report", "updated_at"])

            return Response(
                {
                    "status": "success",
                    "message": "Multi-video session finalized and report created.",
                    "session_id": session.id,
                    "report_id": report.id,
                    "report_owner_user_id": target_user.id,
                    "report_owner_email": target_user.email,
                    "s3_key": s3_key_uploaded,
                    "s3_url": s3_url_uploaded,
                    "total_matches": match_count,
                    "total_videos": len(active_items),
                },
                status=status.HTTP_200_OK,
            )

        except CreditCommitError as exc:
            if s3_key_uploaded:
                try:
                    s3.delete_files([s3_key_uploaded])
                except Exception:
                    pass
            return Response(
                {"status": "blocked", "code": "INSUFFICIENT_CREDITS", "message": str(exc), "match_count": match_count},
                status=402,
            )
        except IntegrityError:
            if s3_key_uploaded:
                try:
                    s3.delete_files([s3_key_uploaded])
                except Exception:
                    pass
            return Response(
                {"status": "duplicate", "message": "This generated file already exists for the user."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            if s3_key_uploaded:
                try:
                    s3.delete_files([s3_key_uploaded])
                except Exception:
                    pass
            return Response(
                {"error": "An unexpected error occurred.", "detail": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class MultiVideoSessionDownloadXlsxView(APIView):
    """
    GET → Download the finalized Excel report for the multi-video session.
    """
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def get(self, request, session_id):
        session = _get_session_or_404(session_id, request.user)
        if not session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)

        if session.status != MultiVideoSession.STATUS_COMPLETED:
            return Response({"error": "Session is not finalized yet."}, status=status.HTTP_400_BAD_REQUEST)

        report = session.generated_report
        if not report:
            return Response({"error": "No generated report linked to this session."}, status=status.HTTP_404_NOT_FOUND)
        if not report.s3_key:
            return Response({"error": "Report file key is missing."}, status=status.HTTP_404_NOT_FOUND)

        s3 = S3Service()
        download_url = s3.generate_presigned_get_url(
            report.s3_key,
            expires_in=3600,
            download_filename=report.filename or "report.xlsx",
        )
        if not download_url:
            return Response({"error": "Failed to generate download URL."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(
            {
                "status": "success",
                "session_id": session.id,
                "report_id": report.id,
                "filename": report.filename,
                "s3_key": report.s3_key,
                "download_url": download_url,
                "expires_in_seconds": 3600,
            },
            status=status.HTTP_200_OK,
        )
