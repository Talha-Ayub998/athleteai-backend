from collections import defaultdict
from io import BytesIO
from datetime import datetime
from pathlib import Path

import pandas as pd
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from athleteai.permissions import BlockSuperUserPermission
from reports.models import (
    AnnotationEvent,
    AnnotationMatchResult,
    AnnotationSession,
    AthleteReport,
    VideoUrl,
)
from reports.serializers import (
    AnnotationEventSerializer,
    AnnotationMatchResultSerializer,
    AnnotationSessionSerializer,
)
from users.credit_service import CreditCommitError, commit_credit, reserve_credit
from utils.excel_to_pdf import count_matches, process_excel_file
from utils.helpers import get_file_hash
from utils.s3_service import S3Service


def _session_for_user_or_404(session_id, user):
    return AnnotationSession.objects.filter(id=session_id, user=user).first()


def _normalize_errors(raw_errors):
    if raw_errors is None:
        return []
    if isinstance(raw_errors, list):
        return [str(err) for err in raw_errors]
    return [str(raw_errors)]


def _normalize_athlete_profile(user, payload):
    payload = payload or {}
    fallback_name = (getattr(user, "username", "") or "").strip() or user.email.split("@")[0]
    full_name = (getattr(user, "get_full_name", lambda: "")() or "").strip()
    return {
        "name": str(payload.get("name") or full_name or fallback_name).strip(),
        "email": str(payload.get("email") or user.email).strip(),
        "belt": str(payload.get("belt") or "Unknown").strip(),
        "gym": str(payload.get("gym") or "Unknown").strip(),
        "language": str(payload.get("language") or "English").strip(),
    }


def _build_dated_filename(base_name: str, now_dt):
    stem = Path(base_name).stem.strip() or "Report"
    date_str = now_dt.strftime("%Y-%m-%d")
    return f"{stem}_{date_str}.xlsx"


def _aggregate_stats_rows(events):
    counters = defaultdict(
        lambda: {
            "offense_attempted": 0,
            "offense_succeeded": 0,
            "defense_attempted": 0,
            "defense_succeeded": 0,
        }
    )
    match_numbers = set()

    for event in events:
        if event.event_type == AnnotationEvent.EVENT_NOTE:
            continue
        if event.player not in {AnnotationEvent.PLAYER_ME, AnnotationEvent.PLAYER_OPPONENT}:
            continue
        if not event.move_name:
            continue
        if event.outcome not in {AnnotationEvent.OUTCOME_SUCCESS, AnnotationEvent.OUTCOME_FAILED}:
            continue

        move_name = str(event.move_name).strip()
        key = (event.match_number, move_name)
        match_numbers.add(event.match_number)

        if event.player == AnnotationEvent.PLAYER_ME:
            counters[key]["offense_attempted"] += 1
            if event.outcome == AnnotationEvent.OUTCOME_SUCCESS:
                counters[key]["offense_succeeded"] += 1
        else:
            counters[key]["defense_attempted"] += 1
            if event.outcome == AnnotationEvent.OUTCOME_FAILED:
                counters[key]["defense_succeeded"] += 1

    return counters, sorted(match_numbers)


def _build_workbook_bytes(athlete_profile, stats_counters, match_numbers, match_results_map):
    athlete_df = pd.DataFrame(
        [
            {
                "Name": athlete_profile["name"],
                "Email": athlete_profile["email"],
                "Belt": athlete_profile["belt"],
                "Gym": athlete_profile["gym"],
                "Language": athlete_profile["language"],
            }
        ]
    )
    input_validation_df = pd.DataFrame(
        [
            {
                "GeneratedBy": "annotation_session_api",
                "GeneratedAtUTC": datetime.utcnow().isoformat(timespec="seconds"),
            }
        ]
    )

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        input_validation_df.to_excel(writer, sheet_name="InputValidation", index=False)
        athlete_df.to_excel(writer, sheet_name="Athlete", index=False)

        for match_number in match_numbers:
            match_label = f"Match-{match_number}"
            stats_rows = []

            for (m_num, move_name), values in sorted(stats_counters.items(), key=lambda x: (x[0][0], x[0][1].lower())):
                if m_num != match_number:
                    continue
                stats_rows.append(
                    {
                        "move_name": move_name,
                        "offense_attempted": int(values["offense_attempted"]),
                        "offense_succeeded": int(values["offense_succeeded"]),
                        "defense_attempted": int(values["defense_attempted"]),
                        "defense_succeeded": int(values["defense_succeeded"]),
                        "match": match_label,
                    }
                )

            stats_df = pd.DataFrame(
                stats_rows,
                columns=[
                    "move_name",
                    "offense_attempted",
                    "offense_succeeded",
                    "defense_attempted",
                    "defense_succeeded",
                    "match",
                ],
            )
            stats_df.to_excel(writer, sheet_name=f"{match_label} Stats", index=False)

            result = match_results_map[match_number]
            result_df = pd.DataFrame(
                [
                    {
                        "Result": result.result,
                        "Match Type": result.match_type,
                        "Referee Decision": "Yes" if result.referee_decision else "No",
                        "Disqualified?": "Yes" if result.disqualified else "No",
                        "Opponent": result.opponent,
                    }
                ]
            )
            result_df.to_excel(writer, sheet_name=f"{match_label} Result", index=False)

    output.seek(0)
    return output.getvalue()


def _missing_previous_match_results(session, target_match_number):
    if target_match_number <= 1:
        return []

    required_previous_matches = set(range(1, target_match_number))

    existing_results = set(
        session.match_results.filter(match_number__lt=target_match_number, match_number__gte=1)
        .values_list("match_number", flat=True)
        .distinct()
    )
    return sorted(required_previous_matches - existing_results)


class AnnotationSessionListCreateView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def get(self, request):
        if request.body and request.body.strip():
            return Response(
                {
                    "error": "GET /annotation-sessions/ does not accept a request body.",
                    "hint": "Use POST /annotation-sessions/ to create a session.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        queryset = (
            AnnotationSession.objects.filter(user=request.user)
            .select_related("video")
            .annotate(
                events_count=Count("events", distinct=True),
                match_results_count=Count("match_results", distinct=True),
            )
            .order_by("-created_at")
        )
        serializer = AnnotationSessionSerializer(queryset, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = AnnotationSessionSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated = serializer.validated_data
        video = validated.get("video")
        video_url = validated.get("video_url")

        existing_draft_qs = AnnotationSession.objects.filter(
            user=request.user,
            status=AnnotationSession.STATUS_DRAFT,
        )
        if video is not None:
            existing_draft_qs = existing_draft_qs.filter(video=video)
        elif video_url:
            existing_draft_qs = existing_draft_qs.filter(video_url=video_url)
        else:
            existing_draft_qs = existing_draft_qs.none()

        existing_draft = existing_draft_qs.order_by("-updated_at", "-id").first()
        if existing_draft:
            existing_payload = AnnotationSessionSerializer(existing_draft, context={"request": request}).data
            return Response(
                {
                    "status": "existing_draft",
                    "message": "Draft session already exists for this video. Reusing existing session.",
                    **existing_payload,
                },
                status=status.HTTP_200_OK,
            )

        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class AnnotationLatestSessionByVideoView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def get(self, request):
        video_id = request.query_params.get("video_id")
        queryset = (
            AnnotationSession.objects
            .filter(user=request.user, video__isnull=False)
            .select_related("video")
            .annotate(
                events_count=Count("events", distinct=True),
                match_results_count=Count("match_results", distinct=True),
            )
            .order_by("-created_at", "-id")
        )

        if video_id is not None:
            try:
                video_id_int = int(video_id)
            except (TypeError, ValueError):
                return Response({"error": "video_id must be an integer."}, status=status.HTTP_400_BAD_REQUEST)
            queryset = queryset.filter(video_id=video_id_int)

        latest_by_video = {}
        for session in queryset:
            if session.video_id not in latest_by_video:
                latest_by_video[session.video_id] = session

        sessions = list(latest_by_video.values())
        serializer = AnnotationSessionSerializer(sessions, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class AnnotationSessionDetailView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def get(self, request, session_id):
        session = _session_for_user_or_404(session_id, request.user)
        if not session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)

        session_data = AnnotationSessionSerializer(session, context={"request": request}).data
        events_data = AnnotationEventSerializer(session.events.all().order_by("match_number", "timestamp_seconds", "id"), many=True).data
        match_results_data = AnnotationMatchResultSerializer(session.match_results.all().order_by("match_number"), many=True).data

        return Response(
            {
                "session": session_data,
                "events": events_data,
                "match_results": match_results_data,
            },
            status=status.HTTP_200_OK,
        )

    def patch(self, request, session_id):
        session = _session_for_user_or_404(session_id, request.user)
        if not session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
        if session.status != AnnotationSession.STATUS_DRAFT:
            return Response({"error": "Completed sessions cannot be edited."}, status=status.HTTP_409_CONFLICT)

        serializer = AnnotationSessionSerializer(
            session,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)


class AnnotationEventCreateView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def post(self, request, session_id):
        session = _session_for_user_or_404(session_id, request.user)
        if not session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
        if session.status != AnnotationSession.STATUS_DRAFT:
            return Response({"error": "Completed sessions cannot be edited."}, status=status.HTTP_409_CONFLICT)

        serializer = AnnotationEventSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        match_number = serializer.validated_data["match_number"]
        missing_previous = _missing_previous_match_results(session, match_number)
        if missing_previous:
            return Response(
                {
                    "error": "Previous match results are missing.",
                    "missing_match_numbers": missing_previous,
                    "message": f"Create match-results for Match-{missing_previous[0]} before moving to Match-{match_number}.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer.save(session=session)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class AnnotationEventDetailView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def patch(self, request, session_id, event_id):
        session = _session_for_user_or_404(session_id, request.user)
        if not session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
        if session.status != AnnotationSession.STATUS_DRAFT:
            return Response({"error": "Completed sessions cannot be edited."}, status=status.HTTP_409_CONFLICT)

        event = session.events.filter(id=event_id).first()
        if not event:
            return Response({"error": "Event not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = AnnotationEventSerializer(event, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        match_number = serializer.validated_data.get("match_number", event.match_number)
        missing_previous = _missing_previous_match_results(session, match_number)
        if missing_previous:
            return Response(
                {
                    "error": "Previous match results are missing.",
                    "missing_match_numbers": missing_previous,
                    "message": f"Create match-results for Match-{missing_previous[0]} before moving to Match-{match_number}.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, session_id, event_id):
        session = _session_for_user_or_404(session_id, request.user)
        if not session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
        if session.status != AnnotationSession.STATUS_DRAFT:
            return Response({"error": "Completed sessions cannot be edited."}, status=status.HTTP_409_CONFLICT)

        event = session.events.filter(id=event_id).first()
        if not event:
            return Response({"error": "Event not found."}, status=status.HTTP_404_NOT_FOUND)

        event.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AnnotationMatchResultListCreateView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def get(self, request, session_id):
        session = _session_for_user_or_404(session_id, request.user)
        if not session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = AnnotationMatchResultSerializer(session.match_results.all().order_by("match_number"), many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request, session_id):
        session = _session_for_user_or_404(session_id, request.user)
        if not session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
        if session.status != AnnotationSession.STATUS_DRAFT:
            return Response({"error": "Completed sessions cannot be edited."}, status=status.HTTP_409_CONFLICT)

        serializer = AnnotationMatchResultSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        payload = serializer.validated_data
        match_number = payload["match_number"]
        has_events_for_match = session.events.filter(match_number=match_number).exists()
        existing_result = session.match_results.filter(match_number=match_number).first()
        if not has_events_for_match and not existing_result:
            return Response(
                {
                    "error": "Cannot create match result without events.",
                    "message": f"Add at least one event for Match-{match_number} before creating its result.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        obj, created = AnnotationMatchResult.objects.update_or_create(
            session=session,
            match_number=match_number,
            defaults={
                "result": payload["result"],
                "match_type": payload.get("match_type") or "No-GI Points",
                "referee_decision": payload.get("referee_decision", False),
                "disqualified": payload.get("disqualified", False),
                "opponent": payload.get("opponent") or "Unknown Opponent",
            },
        )
        response_serializer = AnnotationMatchResultSerializer(obj)
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AnnotationMatchResultDetailView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def patch(self, request, session_id, result_id):
        session = _session_for_user_or_404(session_id, request.user)
        if not session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
        if session.status != AnnotationSession.STATUS_DRAFT:
            return Response({"error": "Completed sessions cannot be edited."}, status=status.HTTP_409_CONFLICT)

        obj = session.match_results.filter(id=result_id).first()
        if not obj:
            return Response({"error": "Match result not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = AnnotationMatchResultSerializer(obj, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        target_match_number = serializer.validated_data.get("match_number", obj.match_number)
        has_events_for_target = session.events.filter(match_number=target_match_number).exists()
        if not has_events_for_target:
            return Response(
                {
                    "error": "Cannot save match result without events.",
                    "message": f"Add at least one event for Match-{target_match_number} before saving its result.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, session_id, result_id):
        session = _session_for_user_or_404(session_id, request.user)
        if not session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
        if session.status != AnnotationSession.STATUS_DRAFT:
            return Response({"error": "Completed sessions cannot be edited."}, status=status.HTTP_409_CONFLICT)

        obj = session.match_results.filter(id=result_id).first()
        if not obj:
            return Response({"error": "Match result not found."}, status=status.HTTP_404_NOT_FOUND)

        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AnnotationFinalizeView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def post(self, request, session_id):
        session = _session_for_user_or_404(session_id, request.user)
        if not session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)

        if session.status == AnnotationSession.STATUS_COMPLETED and session.generated_report_id:
            return Response(
                {
                    "status": "already_completed",
                    "session_id": session.id,
                    "report_id": session.generated_report_id,
                    "message": "This session is already finalized.",
                },
                status=status.HTTP_200_OK,
            )
        if session.status != AnnotationSession.STATUS_DRAFT:
            return Response({"error": "Session is not in draft state."}, status=status.HTTP_409_CONFLICT)

        events = list(session.events.all())
        stats_counters, match_numbers = _aggregate_stats_rows(events)
        if not match_numbers:
            return Response(
                {
                    "error": "No valid stat events found.",
                    "message": "Add at least one non-note event with match_number, move_name, player, and outcome.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = list(session.match_results.filter(match_number__in=match_numbers))
        match_results_map = {obj.match_number: obj for obj in results}
        missing = [m for m in match_numbers if m not in match_results_map]
        if missing:
            return Response(
                {
                    "error": "Missing match results.",
                    "missing_match_numbers": missing,
                    "message": "Add one match result entry for each annotated match before finalizing.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        athlete_profile = _normalize_athlete_profile(request.user, request.data.get("athlete"))
        xlsx_bytes = _build_workbook_bytes(athlete_profile, stats_counters, match_numbers, match_results_map)

        now_dt = timezone.now()
        fallback_name = athlete_profile["name"].replace(" ", "") or f"user_{request.user.id}"
        requested_filename = request.data.get("filename") or fallback_name
        filename = _build_dated_filename(str(requested_filename), now_dt)

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

        is_admin = getattr(request.user, "role", None) == "admin"
        must_check_credits = not is_admin
        ticket = None
        if must_check_credits:
            ok, ticket, msg = reserve_credit(request.user, units=match_count)
            if not ok:
                return Response(
                    {
                        "status": "blocked",
                        "code": "INSUFFICIENT_CREDITS",
                        "message": msg,
                        "match_count": match_count,
                    },
                    status=402,
                )
            if getattr(ticket, "source", None) == "one_time" and match_count < 4:
                return Response(
                    {
                        "status": "blocked",
                        "message": "One-time PDF requires at least 4 matches.",
                        "match_count": match_count,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            excel_file.seek(0)
            file_hash = get_file_hash(excel_file)
        except Exception:
            return Response({"error": "Failed to hash generated workbook."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        duplicate_report = AthleteReport.objects.filter(user=request.user, file_hash=file_hash).first()
        if duplicate_report:
            return Response(
                {
                    "status": "duplicate",
                    "message": "This generated file already exists for the user.",
                    "existing_report_id": duplicate_report.id,
                    "existing_filename": duplicate_report.filename,
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
            return Response(
                {"status": "error", "message": "Invalid processor response."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        result, success = processed[0], processed[1]
        if not success:
            return Response(
                {
                    "status": "error",
                    "message": "Validation failed.",
                    "errors": _normalize_errors(result),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        s3 = S3Service()
        s3_key_uploaded = None
        s3_url_uploaded = None
        try:
            excel_file.seek(0)
            s3_result = s3.upload_files([excel_file], user_id=request.user.id)
            if not s3_result or "key" not in s3_result[0]:
                return Response({"error": "Failed to upload generated file to storage."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            s3_key_uploaded = s3_result[0]["key"]
            s3_url_uploaded = s3_result[0].get("url")

            file_size_mb = round(getattr(excel_file, "size", 0) / (1024 * 1024), 2)

            with transaction.atomic():
                report = AthleteReport.objects.create(
                    user=request.user,
                    filename=filename,
                    pdf_data=result,
                    file_size_mb=file_size_mb,
                    file_hash=file_hash,
                    s3_key=s3_key_uploaded,
                )
                if must_check_credits and ticket:
                    commit_credit(ticket)

                session.status = AnnotationSession.STATUS_COMPLETED
                session.finalized_at = timezone.now()
                session.generated_report = report
                session.save(update_fields=["status", "finalized_at", "generated_report", "updated_at"])

            return Response(
                {
                    "status": "success",
                    "message": "Annotation session finalized and report created.",
                    "session_id": session.id,
                    "report_id": report.id,
                    "s3_key": s3_key_uploaded,
                    "s3_url": s3_url_uploaded,
                    "match_count": match_count,
                    "credit_source": "admin_bypass" if is_admin else getattr(ticket, "source", None),
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
                {
                    "status": "blocked",
                    "code": "INSUFFICIENT_CREDITS",
                    "message": str(exc),
                    "match_count": match_count,
                },
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


class AnnotationSessionDownloadXlsxView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def get(self, request, session_id):
        session = _session_for_user_or_404(session_id, request.user)
        if not session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)

        if session.status != AnnotationSession.STATUS_COMPLETED:
            return Response(
                {"error": "Session is not completed yet."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        report = session.generated_report
        if not report:
            return Response(
                {"error": "No generated report linked to this session."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not report.s3_key:
            return Response(
                {"error": "Generated report file key is missing."},
                status=status.HTTP_404_NOT_FOUND,
            )

        s3 = S3Service()
        download_url = s3.generate_presigned_get_url(
            report.s3_key,
            expires_in=3600,
            download_filename=report.filename or "report.xlsx",
        )
        if not download_url:
            return Response(
                {"error": "Failed to generate download URL."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

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
