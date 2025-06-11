from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from utils.s3_service import S3Service
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi


class UploadExcelFileView(APIView):
    parser_classes = [MultiPartParser]
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description="Upload multiple Excel or PDF files.",
        manual_parameters=[
            openapi.Parameter(
                'file',
                in_=openapi.IN_FORM,
                type=openapi.TYPE_FILE,
                description='Multiple files',
                required=True
            )
        ],
        responses={201: 'Files uploaded'}
    )
    def post(self, request):
        try:
            files = request.FILES.getlist("file")
            if not files:
                return Response({"error": "No files provided."}, status=400)

            s3 = S3Service()
            results = s3.upload_files(files, user_id=request.user.id)
            return Response({"message": "Upload complete", "results": results}, status=201)

        except Exception as e:
            print(f"Upload error: {e}")
            return Response({"error": "An unexpected error occurred."}, status=500)


class ListUserFilesView(APIView):
    """
    API endpoint for authenticated users to list all files they have uploaded to S3.
    Files are fetched from their user-specific folder in the S3 bucket.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description="List all files uploaded by the authenticated user.",
        responses={200: 'List of uploaded files'}
    )
    def get(self, request):
        try:
            s3 = S3Service()
            files = s3.list_user_files(user_id=request.user.id)
            return Response(files, status=status.HTTP_200_OK)

        except Exception as e:
            print(f"List files error: {e}")  # You can replace this with logging
            return Response({"error": "Failed to fetch file list."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



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
