from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from utils.s3_service import S3Service
from utils.excel_to_pdf import process_excel_file
from utils.helpers import get_file_hash
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.db.models import Q
from users.models import CustomUser
from athleteai.permissions import BlockSuperUserPermission
from rest_framework.pagination import PageNumberPagination
from rest_framework.generics import ListAPIView
from collections import defaultdict
import re

from reports.models import AthleteReport, VideoUrl
from reports.serializers import VideoUrlSerializer, VideoUrlReadSerializer

class UploadExcelFileView(APIView):
    parser_classes = [MultiPartParser]
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    @swagger_auto_schema(
    operation_description="Upload an Excel file. Admins can upload on behalf of users by providing `user_id`.",
    manual_parameters=[
        openapi.Parameter(
            name="file",
            in_=openapi.IN_FORM,
            type=openapi.TYPE_FILE,
            required=True,
            description="Excel .xlsx file to be uploaded",
        ),
        openapi.Parameter(
            name="user_id",
            in_=openapi.IN_FORM,
            type=openapi.TYPE_INTEGER,
            required=False,
            description="User ID to upload report on behalf of (admin only)",
        ),
    ],
    responses={
        200: openapi.Response(description="Success"),
        400: "Invalid file or duplicate upload",
        403: "Permission denied",
        500: "Internal server error",
    },
    )
    def post(self, request):
        """
        Uploads a single .xlsx, validates & processes it, stores in S3, then records DB metadata.
        Critical fixes:
        - Rewind (seek(0)) after each read (hashing/processing) and before S3 upload
        - Tolerate common Excel MIME types (some browsers send octet-stream)
        """
        try:
            # ---- 1) Extract file -------------------------------------------------
            files = request.FILES.getlist("file")
            if not files:
                return Response({"error": "No files provided."}, status=400)

            excel_file = files[0]
            filename = excel_file.name

            # Accept typical Excel types; some clients send application/octet-stream
            allowed_suffix = filename.lower().endswith(".xlsx")
            allowed_cts = {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/octet-stream",
                "application/vnd.ms-excel",  # occasionally seen
            }
            if not allowed_suffix:
                return Response({"error": "Only .xlsx Excel files are allowed."}, status=400)

            # ---- 2) Resolve target user (admin may upload for athlete) ----------
            target_user = request.user
            user_id = request.data.get("user_id")
            if user_id:
                if getattr(request.user, "role", None) != "admin":
                    return Response({"error": "Only admins can upload reports for other users."}, status=403)
                try:
                    target_user = CustomUser.objects.get(id=user_id, role="athlete")
                except CustomUser.DoesNotExist:
                    return Response({"error": "Invalid athlete user_id provided."}, status=400)

            # ---- 3) Hash to detect duplicates -----------------------------------
            try:
                excel_file.seek(0)
            except Exception:
                pass
            file_hash = get_file_hash(excel_file)

            duplicate_report = AthleteReport.objects.filter(
                user=target_user, file_hash=file_hash
            ).first()
            if duplicate_report:
                return Response(
                    {
                        "status": "duplicate",
                        "message": "This file has already been uploaded by the user.",
                        "existing_filename": duplicate_report.filename,
                        "uploaded_at": getattr(duplicate_report, "created_at", None),
                    },
                    status=400,
                )

            # ---- 4) Process & validate ------------------------------------------
            try:
                excel_file.seek(0)
            except Exception:
                pass
            result, success = process_excel_file(excel_file)
            if not success:
                return Response(
                    {"status": "error", "message": "Validation failed.", "errors": result},
                    status=400,
                )

            # ---- 5) Upload to S3 (rewind again BEFORE upload) -------------------
            try:
                excel_file.seek(0)
            except Exception:
                pass

            s3 = S3Service()
            s3_result = s3.upload_files([excel_file], user_id=target_user.id)
            if not s3_result or "key" not in s3_result[0]:
                return Response({"error": "Failed to upload file to storage."}, status=500)

            s3_key_uploaded = s3_result[0]["key"]
            s3_url_uploaded = s3_result[0].get("url")

            # ---- 6) Save DB record ----------------------------------------------
            file_size_mb = round(getattr(excel_file, "size", 0) / (1024 * 1024), 2)
            AthleteReport.objects.create(
                user=target_user,
                filename=filename,
                pdf_data=result,
                file_size_mb=file_size_mb,
                file_hash=file_hash,
                s3_key=s3_key_uploaded,
            )

            # ---- 7) Done ---------------------------------------------------------
            return Response(
                {
                    "status": "success",
                    "message": f"Report uploaded successfully for {target_user.email}.",
                    "s3_key": s3_key_uploaded,
                    "s3_url": s3_url_uploaded,
                },
                status=200,
            )

        except Exception as e:
            print(f"Upload error: {e}")
            return Response({"error": "An unexpected error occurred."}, status=500)


class ListUserReportsView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    @swagger_auto_schema(
        operation_description=(
            "Admins can view all athlete reports and their own reports; "
            "athletes can view only their own. Superusers are not allowed.\n\n"
            "Response is grouped by user: each user has `reports` and `video_urls`."
        ),
        manual_parameters=[
            openapi.Parameter(
                name="q",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Optional: filter included video URLs by partial match (icontains) on URL.",
                required=False
            ),
        ],
        responses={200: "Users with reports and video URLs", 403: "Forbidden", 500: "Server error"}
    )
    def get(self, request):
        try:
            user = request.user

            # --- visibility
            if user.role == 'admin':
                reports_qs = (
                    AthleteReport.objects
                    .filter(Q(user__role='athlete') | Q(user=user))
                    .exclude(~Q(user=user) & Q(user__role='admin'))
                )
            else:
                reports_qs = AthleteReport.objects.filter(user=user)

            reports_qs = reports_qs.select_related("user").order_by('-uploaded_at')

            # --- group reports by user_id
            reports_by_user = defaultdict(list)
            user_meta = {}  # user_id -> {"user_id": ..., "email": ...}
            for r in reports_qs:
                user_meta[r.user_id] = {"user_id": r.user_id, "email": r.user.email}
                reports_by_user[r.user_id].append({
                    "id": r.id,
                    "filename": r.filename,
                    "uploaded_at": r.uploaded_at,
                    "file_size_mb": r.file_size_mb,
                    "pdf_data": r.pdf_data,
                })

            # if no reports, return empty (and avoid extra video query)
            if not reports_by_user:
                return Response([], status=status.HTTP_200_OK)

            # --- fetch videos for just those users (one query), optional filter
            user_ids = list(reports_by_user.keys())
            videos_qs = VideoUrl.objects.filter(user_id__in=user_ids).order_by("-created_at")

            q = request.query_params.get("q")
            if q:
                videos_qs = videos_qs.filter(url__icontains=q)

            videos_by_user = defaultdict(list)
            for v in videos_qs:
                videos_by_user[v.user_id].append({
                    "id": v.id,
                    "url": v.url,
                    "created_at": v.created_at,
                })

            # --- merge: one object per user
            users_payload = []
            for uid, meta in user_meta.items():
                users_payload.append({
                    "user_id": meta["user_id"],
                    "email": meta["email"],
                    "reports": reports_by_user.get(uid, []),
                    "video_urls": videos_by_user.get(uid, []),
                })

            # optional: sort users by email (or by most recent report)
            users_payload.sort(key=lambda x: x["email"].lower())

            return Response(users_payload, status=status.HTTP_200_OK)

        except Exception as e:
            print("ListUserReportsView error:", e)
            return Response({"error": "Failed to fetch report list."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)



class DeleteUserFileView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    @swagger_auto_schema(
        operation_description="Authenticated users (admins or athletes) can delete their own uploaded reports. Superusers are not allowed.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["ids"],
            properties={
                'ids': openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Items(type=openapi.TYPE_INTEGER))
            },
        ),
        responses={200: 'Deletion result list'}
    )
    def delete(self, request):
        ids = request.data.get("ids")
        if not isinstance(ids, list) or not ids:
            return Response({"error": "Provide a list of file IDs."}, status=400)

        user = request.user

        # ✅ Allow admin or athlete to delete their own reports only
        reports = AthleteReport.objects.filter(id__in=ids, user=user)

        if not reports.exists():
            return Response({"error": "No matching files found or you are not authorized to delete them."}, status=404)

        # Delete from S3
        s3_keys = [report.s3_key for report in reports if report.s3_key]

        s3 = S3Service()
        s3_results = s3.delete_files(s3_keys)

        # Delete from DB
        deleted_count, _ = reports.delete()

        return Response({
            "status": "success",
            "deleted_count": deleted_count,
            "s3_results": s3_results
        }, status=200)


class UploadVideoUrlView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    def post(self, request):
        serializer = VideoUrlSerializer(data=request.data)
        if serializer.is_valid():
            video_url = serializer.validated_data['video_url']

            # Get existing or create new
            obj, created = VideoUrl.objects.get_or_create(
                user=request.user,
                url=video_url,
            )

            if created:
                return Response(
                    {"message": "Video URL saved successfully", "id": obj.id, "url": obj.url},
                    status=status.HTTP_201_CREATED
                )
            else:
                return Response(
                    {"message": "Video URL already exists", "id": obj.id, "url": obj.url},
                    status=status.HTTP_200_OK
                )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class DefaultPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class ListUserVideoUrlsView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = VideoUrlReadSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        qs = VideoUrl.objects.filter(user=self.request.user).order_by("-created_at")
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(Q(url__icontains=q))
        return qs

# views.py
import re
from collections import defaultdict
from datetime import datetime
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from django.db.models import Q

# from .models import AthleteReport
# from .permissions import BlockSuperUserPermission
# from rest_framework.permissions import IsAuthenticated

class ReportKPIsView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    @swagger_auto_schema(
        operation_description=(
            "Return 5 KPI cards + a bar chart (points scored vs conceded) "
            "aggregated across ALL reports for a target user. "
            "Athletes get their own data; admins can pass user_id."
        ),
        manual_parameters=[
            openapi.Parameter(
                name="user_id",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=False,
                description="Admin-only: athlete user_id to aggregate. Athletes ignore this and get their own."
            ),
        ],
        responses={200: "KPIs payload", 403: "Forbidden"}
    )
    def get(self, request):
        try:
            auth_user = request.user
            q_user_id = request.query_params.get("user_id")

            # ----- visibility
            if auth_user.role == "athlete":
                target_user_id = auth_user.id
            else:  # admin
                if not q_user_id:
                    return Response(
                        {"detail": "user_id is required for admins."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                target_user_id = int(q_user_id)

            # ----- fetch all reports for target user
            reports_qs = (
                AthleteReport.objects
                .filter(user_id=target_user_id)
                .select_related("user")
                .order_by("-uploaded_at")
            )
            if not reports_qs.exists():
                return Response({"detail": "No reports found."}, status=status.HTTP_200_OK)

            payload = self._build_kpis_aggregated(reports_qs)
            return Response(payload, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # ---------------------------
    # Aggregation & parsing utils
    # ---------------------------
    def _build_kpis_aggregated(self, reports_qs):
        total_matches = wins = losses = 0
        offense_succ_vals, defense_succ_vals = [], []

        labels, my_points, opp_points = [], [], []
        points_rows_count = 0
        match_type_badges = set()

        offense_attempts_sum = defaultdict(int)
        defense_attempts_sum = defaultdict(int)

        for r in reports_qs:
            d = r.pdf_data or {}
            for mt in d.get("match_types", []) or []:
                match_type_badges.add(mt)

            wl = d.get("win/loss_ratio") or []
            if len(wl) >= 3:
                total_matches += self._extract_first_int(wl[0])
                wins += self._extract_first_int(wl[1])
                losses += self._extract_first_int(wl[2])

            subs = d.get("submissions") or []
            offense_succ_vals.append(self._extract_percent_by_key(subs, "Offensive Submission Success Ratio"))
            defense_succ_vals.append(self._extract_percent_by_key(subs, "Defensive Submission Success Ratio"))

            for row in d.get("points", []) or []:
                parsed = self._parse_points_row(row)
                if parsed:
                    match_label, mine, opp = parsed
                    labels.append(match_label)
                    my_points.append(mine)
                    opp_points.append(opp)
                    points_rows_count += 1

            graph = d.get("graph_data") or {}
            self._accumulate_attempts(graph.get("offense_attempts"), offense_attempts_sum)
            self._accumulate_attempts(graph.get("defense_attempts"), defense_attempts_sum)

        # --- derived KPIs
        win_rate = (wins / total_matches) if total_matches else None
        offensive_submission_success = self._avg_clean(offense_succ_vals)
        defensive_submission_success = self._avg_clean(defense_succ_vals)

        denom = points_rows_count if points_rows_count else None
        avg_points_per_match = (sum(my_points) / denom) if denom else None
        partial_points = (denom is not None and total_matches and denom != total_matches)

        top_offensive_move = self._top_move_from_map(offense_attempts_sum)
        top_defensive_threat = self._top_move_from_map(defense_attempts_sum)

        return {
            "user_id": reports_qs[0].user_id,
            "user_email": reports_qs[0].user.email,
            "matches_total": total_matches,
            "wins": wins,
            "losses": losses,
            "kpis": {
                # "win_rate": win_rate,
                "win_rate_pct": self._format_pct(win_rate),

                # "offensive_submission_success": offensive_submission_success,
                "offensive_submission_success_pct": self._format_pct(offensive_submission_success),

                # "defensive_submission_success": defensive_submission_success,
                "defensive_submission_success_pct": self._format_pct(defensive_submission_success),

                "avg_points_per_match": avg_points_per_match,
                # "avg_points_per_match_pct": self._format_pct(avg_points_per_match),  # optional, might not make sense as %
                
                "top_moves": {
                    "top_offensive_move": top_offensive_move,
                    "top_defensive_threat": top_defensive_threat
                }
            },
            "chart": {
                "points_bar": {
                    "labels": labels,
                    "my_points": my_points,
                    "opp_points": opp_points,
                    "partial_points": partial_points
                }
            },
            "badges": sorted(match_type_badges),
        }


    # ---- helpers
    def _extract_first_int(self, text, default=0):
        m = re.search(r"\d+", str(text))
        return int(m.group(0)) if m else default

    def _extract_percent_by_key(self, lines, key):
        """
        Finds a line containing 'key' and returns a float ratio (e.g., '25.76%' -> 0.2576).
        """
        for line in lines:
            if key in str(line):
                m = re.search(r"([\d.]+)\s*%", str(line))
                if m:
                    return float(m.group(1)) / 100.0
        return None

    def _parse_points_row(self, row):
        """
        Parses: "Match-9 - 9.0 – 0.0 Points" or returns None if 'Not Applicable'.
        Handles hyphen/en-dash variants.
        """
        if "Not Applicable" in str(row):
            return None
        # accept "Match-9" or "Match-12" labels
        m = re.search(
            r"(Match-?\s*\d+).*?(\d+(?:\.\d+)?)\s*[–-]\s*(\d+(?:\.\d+)?)",
            str(row)
        )
        if not m:
            return None
        label = re.sub(r"\s+", "", m.group(1)).replace("Match", "Match-")  # normalize "Match 9" -> "Match-9"
        mine = float(m.group(2))
        opp = float(m.group(3))
        return (label, mine, opp)

    def _accumulate_attempts(self, block, bucket: dict):
        """
        block = {"labels": [...], "values": [...]}
        Sums attempts by label across reports.
        """
        if not block:
            return
        labels = block.get("labels") or []
        values = block.get("values") or []
        for i, lbl in enumerate(labels):
            try:
                bucket[lbl] += int(values[i])
            except Exception:
                # skip malformed rows
                continue

    def _top_move_from_map(self, mp: dict):
        if not mp:
            return None
        label, attempts = max(mp.items(), key=lambda kv: kv[1])
        return f"{label} ({attempts} attempts)"

    def _avg_clean(self, arr):
        vals = [v for v in arr if isinstance(v, (int, float))]
        return (sum(vals) / len(vals)) if vals else None
    
    def _format_pct(self, value, digits=2):
        if value is None:
            return None
        return f"{round(value * 100, digits):.{digits}f}%"

