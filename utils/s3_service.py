# services/s3_service.py
import os
import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
import uuid
from datetime import datetime

load_dotenv()  # Load from .env

class S3Service:
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION'),
        )
        self.bucket_name = os.getenv('AWS_STORAGE_BUCKET_NAME')

    def upload_file(self, file_obj, user_id):

        safe_name = file_obj.name.replace(" ", "_")
        filename = f"{uuid.uuid4()}_{safe_name}"
        key = f"user_uploads/{user_id}/{filename}"

        try:
            self.s3_client.upload_fileobj(file_obj, self.bucket_name, key)
            return {
                "key": key,
                "url": f"https://{self.bucket_name}.s3.{os.getenv('AWS_REGION')}.amazonaws.com/{key}",
                "name": safe_name  # include original filename in response
            }
        except ClientError as e:
            print("Upload error:", e)
            return None


    def list_user_files(self, user_id):
        prefix = f"user_uploads/{user_id}/"
        response = self.s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=prefix)

        files = []
        for item in response.get("Contents", []):
            key = item["Key"]
            full_filename = key.split("/")[-1]

            # Extract original name from key format: <uuid>_<original_filename>
            if "_" in full_filename:
                original_name = full_filename.split("_", 1)[1]
            else:
                original_name = full_filename  # fallback in case UUID wasn't used

            files.append({
                "key": key,
                "stored_name": full_filename,
                "original_name": original_name,
                "size_bytes": item["Size"],
                "last_modified": item["LastModified"].isoformat(),
                "url": f"https://{self.bucket_name}.s3.{os.getenv('AWS_REGION')}.amazonaws.com/{key}"
            })

        return files
    
    def delete_file(self, key):
        """
        Deletes a file from S3 after checking if it exists.
        """
        try:
            # Check if object exists first
            self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
        except self.s3_client.exceptions.ClientError as e:
            if e.response['Error']['Code'] == "404":
                return "not_found"
            else:
                print("HeadObject error:", e)
                return False

        # If exists, proceed to delete
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=key)
            return True
        except Exception as e:
            print(f"Delete error: {e}")
            return False


