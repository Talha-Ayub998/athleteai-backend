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
from reports.models import AthleteReport
from django.db.models import Q
from users.models import CustomUser
from athleteai.permissions import BlockSuperUserPermission


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
        try:

            # Step 1: Parse uploaded file
            files = request.FILES.getlist("file")
            if not files:
                return Response({"error": "No files provided."}, status=400)

            excel_file = files[0]
            filename = excel_file.name

            if not filename.endswith(".xlsx") or excel_file.content_type != "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
                return Response({"error": "Only .xlsx Excel files are allowed."}, status=400)

            # Step 2: Determine the target user
            target_user = request.user  # default: self-upload

            user_id = request.data.get("user_id")
            if user_id:
                if request.user.role != 'admin':
                    return Response({"error": "Only admins can upload reports for other users."}, status=403)
                try:
                    target_user = CustomUser.objects.get(id=user_id, role='athlete')
                except CustomUser.DoesNotExist:
                    return Response({"error": "Invalid athlete user_id provided."}, status=400)

            # Step 3: Check for duplicate upload
            file_hash = get_file_hash(excel_file)
            duplicate_report = AthleteReport.objects.filter(user=target_user, file_hash=file_hash).first()
            if duplicate_report:
                return Response({
                    "status": "duplicate",
                    "message": "This file has already been uploaded by the user.",
                    "existing_filename": duplicate_report.filename,
                    "uploaded_at": duplicate_report.created_at if hasattr(duplicate_report, "created_at") else None
                }, status=400)

            # Step 4: Process and validate
            excel_file.seek(0)
            result, success = process_excel_file(excel_file)

            if not success:
                return Response({"status": "error", "message": "Validation failed.", "errors": result}, status=400)

            # Step 5: Upload to S3
            s3 = S3Service()
            s3_result = s3.upload_files([excel_file], user_id=target_user.id)
            s3_key_uploaded = s3_result[0]["key"] if s3_result else None

            # Step 6: Save to DB
            file_size_mb = round(excel_file.size / (1024 * 1024), 2)
            AthleteReport.objects.create(
                user=target_user,
                filename=filename,
                pdf_data=result,
                file_size_mb=file_size_mb,
                file_hash=file_hash,
                s3_key=s3_key_uploaded
            )

            return Response({
                "status": "success",
                "message": f"Report uploaded successfully for {target_user.email}.",
            }, status=200)

        except Exception as e:
            print(f"Upload error: {e}")
            return Response({"error": "An unexpected error occurred."}, status=500)


class ListUserReportsView(APIView):
    permission_classes = [IsAuthenticated, BlockSuperUserPermission]

    @swagger_auto_schema(
        operation_description="Admins can view all athlete reports. Athletes can view their own reports. Superusers are not allowed.",
        responses={
            200: openapi.Response(description="List of reports"),
            403: "Forbidden",
            500: "Failed to fetch report list",
        }
    )
    def get(self, request):
        try:
            user = request.user

            # ✅ Condition 1: Admin — see athlete reports + own reports, but not other admins
            if user.role == 'admin':
                reports = AthleteReport.objects.filter(
                    Q(user__role='athlete') | Q(user=user)
                ).exclude(
                    ~Q(user=user) & Q(user__role='admin')
                )

            # ✅ Condition 2: Athlete — only see their own reports
            else:  # user.role == 'athlete'
                reports = AthleteReport.objects.filter(user=user)

            reports = reports.order_by('-uploaded_at')

            data = [
                {
                    "id": report.id,
                    "filename": report.filename,
                    "uploaded_at": report.uploaded_at,
                    "file_size_mb": report.file_size_mb,
                    "pdf_data": report.pdf_data,
                    "uploaded_by": report.user.email,
                    "user_id": report.user.id
                }
                for report in reports
            ]

            return Response(data, status=status.HTTP_200_OK)

        except Exception as e:
            print(f"List report error: {e}")
            return Response(
                {"error": "Failed to fetch report list."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


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

