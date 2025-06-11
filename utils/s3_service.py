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


    def upload_files(self, files, user_id):
        """
        Uploads multiple files to S3 for the given user_id.
        """
        uploaded = []

        for file_obj in files:
            safe_name = file_obj.name.replace(" ", "_")
            filename = f"{uuid.uuid4()}_{safe_name}"
            key = f"user_uploads/{user_id}/{filename}"

            try:
                self.s3_client.upload_fileobj(file_obj, self.bucket_name, key)
                uploaded.append({
                    "key": key,
                    "url": f"https://{self.bucket_name}.s3.{os.getenv('AWS_REGION')}.amazonaws.com/{key}",
                    "name": safe_name
                })
            except ClientError as e:
                print(f"Upload error ({safe_name}):", e)
                uploaded.append({"error": f"Failed to upload {safe_name}"})

        return uploaded


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
    
    def delete_files(self, keys):
        """
        Deletes multiple files from S3.
        Returns a list of results per key.
        """
        results = []
        for key in keys:
            try:
                self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
            except self.s3_client.exceptions.ClientError as e:
                if e.response['Error']['Code'] == "404":
                    results.append({"key": key, "status": "not_found"})
                    continue
                else:
                    print(f"HeadObject error: {e}")
                    results.append({"key": key, "status": "error"})
                    continue

            try:
                self.s3_client.delete_object(Bucket=self.bucket_name, Key=key)
                results.append({"key": key, "status": "deleted"})
            except Exception as e:
                print(f"Delete error: {e}")
                results.append({"key": key, "status": "error"})

        return results



