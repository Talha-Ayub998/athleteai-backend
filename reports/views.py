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

import hashlib


class UploadExcelFileView(APIView):
    parser_classes = [MultiPartParser]
    permission_classes = [IsAuthenticated]


    def post(self, request):
        try:
            files = request.FILES.getlist("file")
            if not files:
                return Response({"error": "No files provided."}, status=400)

            excel_file = files[0]
            filename = excel_file.name

            # ✅ Step 1: Validate .xlsx
            if not filename.endswith(".xlsx") or excel_file.content_type != "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
                return Response({"error": "Only .xlsx Excel files are allowed."}, status=400)

            # ✅ Step 2: Compute file hash
            file_hash = get_file_hash(excel_file)

            # ✅ Step 3: Check for duplicates
            duplicate_report = AthleteReport.objects.filter(user=request.user, file_hash=file_hash).first()
            if duplicate_report:
                return Response({
                    "status": "duplicate",
                    "message": "You have already uploaded this file before.",
                    "existing_filename": duplicate_report.filename,
                    "uploaded_at": duplicate_report.created_at if hasattr(duplicate_report, "created_at") else None
                }, status=400)


            # ✅ Step 4: Process the file
            excel_file.seek(0)  # reset file pointer
            result, success = process_excel_file(excel_file)

            if not success:
                return Response({
                    "status": "error",
                    "message": "Validation failed.",
                    "errors": result
                }, status=400)

            # ✅ Step 5: Upload to S3
            s3 = S3Service()
            s3_result = s3.upload_files([excel_file], user_id=request.user.id)

            # ✅ Step 6: Save to DB
            file_size_mb = round(excel_file.size / (1024 * 1024), 2)

            AthleteReport.objects.create(
                user=request.user,
                filename=filename,
                pdf_data=result,
                file_size_mb=file_size_mb,
                file_hash=file_hash
            )

            return Response({
                "status": "success",
                "pdf_data": result,
                "upload_result": s3_result
            }, status=200)

        except Exception as e:
            print(f"Upload error: {e}")
            return Response({"error": "An unexpected error occurred."}, status=500)

class ListUserReportsView(APIView):
    """
    API endpoint to list all reports uploaded by the authenticated user from the database.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            reports = AthleteReport.objects.filter(user=request.user).order_by('-uploaded_at')

            data = [
                {
                    "id": report.id,
                    "filename": report.filename,
                    "uploaded_at": report.uploaded_at,
                    "file_size_mb": report.file_size_mb,
                    # optionally include additional summary info from pdf_data
                }
                for report in reports
            ]

            return Response(data, status=status.HTTP_200_OK)

        except Exception as e:
            print(f"List report error: {e}")
            return Response({"error": "Failed to fetch report list."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)




class DeleteUserFileView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description="Delete multiple files by their keys.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["keys"],
            properties={
                'keys': openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Items(type=openapi.TYPE_STRING))
            },
        ),
        responses={200: 'File deletion result list'}
    )
    def delete(self, request):
        keys = request.data.get("keys")
        if not isinstance(keys, list) or not keys:
            return Response({"error": "Provide a list of file keys."}, status=400)

        # Filter keys to user's own folder
        keys = [k for k in keys if k.startswith(f"user_uploads/{request.user.id}/")]

        s3 = S3Service()
        results = s3.delete_files(keys)

        return Response({"results": results}, status=200)
