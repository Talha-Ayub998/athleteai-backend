from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from utils.s3_service import S3Service
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi


class UploadExcelFileView(APIView):
    """
    API endpoint for authenticated users to upload Excel or PDF files to S3.
    Each file is stored under a folder specific to the user (e.g., user_uploads/<user_id>/).
    """
    parser_classes = [MultiPartParser]
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
    operation_description="Upload an Excel or PDF file.",
    manual_parameters=[
        openapi.Parameter(
            'file',
            in_=openapi.IN_FORM,
            type=openapi.TYPE_FILE,
            description='Excel or PDF file',
            required=True
        )
    ],
    responses={201: 'File uploaded successfully'}
    )
    def post(self, request):
        try:
            file_obj = request.FILES.get("file")
            if not file_obj:
                return Response({"error": "No file provided."}, status=status.HTTP_400_BAD_REQUEST)

            s3 = S3Service()
            upload_result = s3.upload_file(file_obj, user_id=request.user.id)

            if upload_result:
                return Response({"message": "File uploaded successfully.", **upload_result}, status=status.HTTP_201_CREATED)
            else:
                return Response({"error": "Upload to S3 failed."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except Exception as e:
            print(f"Upload error: {e}")  # You can replace this with logging
            return Response({"error": "An unexpected error occurred during upload."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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
    """
    API endpoint to delete a specific user file from S3.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description="Delete a file by its S3 key.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["key"],
            properties={
                'key': openapi.Schema(type=openapi.TYPE_STRING, description='S3 key of the file')
            },
        ),
        responses={200: 'File deleted successfully'}
    )
    def delete(self, request):
        key = request.data.get("key")
        if not key or not key.startswith(f"user_uploads/{request.user.id}/"):
            return Response({"error": "Invalid or unauthorized key."}, status=status.HTTP_400_BAD_REQUEST)

        s3 = S3Service()
        result = s3.delete_file(key)

        if result == "not_found":
            return Response({"error": "File does not exist."}, status=status.HTTP_404_NOT_FOUND)
        elif result is True:
            return Response({"message": "File deleted successfully."}, status=status.HTTP_200_OK)
        else:
            return Response({"error": "Failed to delete file."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
