# services/s3_service.py
import os
import boto3
from botocore.exceptions import ClientError
from botocore.config import Config
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
            config=Config(signature_version='s3v4'),
        )
        self.bucket_name = os.getenv('AWS_STORAGE_BUCKET_NAME')

    def _normalized_key_prefix(self):
        raw = (os.getenv("S3_KEY_PREFIX") or "").strip().strip("/")
        return f"{raw}/" if raw else ""

    def _with_prefix(self, key):
        return f"{self._normalized_key_prefix()}{key.lstrip('/')}"

    def user_uploads_prefix(self, user_id):
        return self._with_prefix(f"user_uploads/{user_id}/")

    def user_videos_prefix(self, user_id):
        return self._with_prefix(f"user_videos/{user_id}/")


    def upload_files(self, files, user_id, use_uuid_prefix=True):
        """
        Uploads multiple files to S3 for the given user_id.
        - Rewinds file objects before upload (critical if they were read earlier)
        - Sends correct ContentType and ContentDisposition
        """
        uploaded = []

        for file_obj in files:
            # Some frameworks leave name on a wrapped file; keep original but sanitize
            safe_name = file_obj.name.replace(" ", "_")
            filename = f"{uuid.uuid4()}_{safe_name}" if use_uuid_prefix else safe_name
            key = self._with_prefix(f"user_uploads/{user_id}/{filename}")

            try:
                # ALWAYS rewind before uploading (file may have been read already)
                try:
                    file_obj.seek(0, os.SEEK_SET)
                except Exception:
                    pass  # some backends may not support seek; most do

                self.s3_client.upload_fileobj(
                    Fileobj=file_obj,
                    Bucket=self.bucket_name,
                    Key=key,
                    ExtraArgs={
                        "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        "ContentDisposition": f'attachment; filename="{safe_name}"',
                        "ACL": "private",
                    },
                )

                uploaded.append({
                    "key": key,
                    "url": f"https://{self.bucket_name}.s3.{os.getenv('AWS_REGION')}.amazonaws.com/{key}",
                    "name": safe_name,
                })
            except ClientError as e:
                print(f"Upload error ({safe_name}):", e)
                uploaded.append({"error": f"Failed to upload {safe_name}"})

        return uploaded

    def upload_video_file(self, file_obj, user_id):
        """
        Upload a single video file for a user and return key/url metadata.
        """
        safe_name = file_obj.name.replace(" ", "_")
        filename = f"{uuid.uuid4()}_{safe_name}"
        key = self._with_prefix(f"user_videos/{user_id}/{filename}")
        content_type = getattr(file_obj, "content_type", None) or "application/octet-stream"

        try:
            try:
                file_obj.seek(0, os.SEEK_SET)
            except Exception:
                pass

            self.s3_client.upload_fileobj(
                Fileobj=file_obj,
                Bucket=self.bucket_name,
                Key=key,
                ExtraArgs={
                    "ContentType": content_type,
                    "ContentDisposition": f'inline; filename="{safe_name}"',
                    "ACL": "private",
                },
            )
            return {
                "key": key,
                "url": f"https://{self.bucket_name}.s3.{os.getenv('AWS_REGION')}.amazonaws.com/{key}",
                "name": safe_name,
            }
        except ClientError as e:
            print(f"Video upload error ({safe_name}):", e)
            return {"error": f"Failed to upload {safe_name}"}

    def build_video_key(self, user_id, file_name):
        safe_name = (file_name or "video").replace(" ", "_")
        filename = f"{uuid.uuid4()}_{safe_name}"
        return self._with_prefix(f"user_videos/{user_id}/{filename}")

    def build_s3_public_url(self, key):
        return f"https://{self.bucket_name}.s3.{os.getenv('AWS_REGION')}.amazonaws.com/{key}"

    def create_multipart_upload(self, key, content_type=None, file_name=None):
        extra_args = {"ACL": "private"}
        if content_type:
            extra_args["ContentType"] = content_type
        if file_name:
            safe_name = file_name.replace(" ", "_")
            extra_args["ContentDisposition"] = f'inline; filename="{safe_name}"'

        try:
            response = self.s3_client.create_multipart_upload(
                Bucket=self.bucket_name,
                Key=key,
                **extra_args,
            )
            return {"upload_id": response.get("UploadId"), "key": key}
        except ClientError as e:
            print(f"Create multipart upload error ({key}):", e)
            return {"error": "Failed to create multipart upload."}

    def generate_presigned_upload_part_url(self, key, upload_id, part_number, expires_in=3600):
        try:
            return self.s3_client.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": self.bucket_name,
                    "Key": key,
                    "UploadId": upload_id,
                    "PartNumber": int(part_number),
                },
                ExpiresIn=expires_in,
            )
        except Exception as e:
            print(f"Presigned upload part URL error ({key}, part {part_number}):", e)
            return None

    def complete_multipart_upload(self, key, upload_id, parts):
        try:
            response = self.s3_client.complete_multipart_upload(
                Bucket=self.bucket_name,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
            return {
                "key": key,
                "url": response.get("Location") or self.build_s3_public_url(key),
                "etag": response.get("ETag"),
            }
        except ClientError as e:
            print(f"Complete multipart upload error ({key}):", e)
            return {"error": "Failed to complete multipart upload."}

    def abort_multipart_upload(self, key, upload_id):
        try:
            self.s3_client.abort_multipart_upload(
                Bucket=self.bucket_name,
                Key=key,
                UploadId=upload_id,
            )
            return {"status": "aborted", "key": key, "upload_id": upload_id}
        except ClientError as e:
            print(f"Abort multipart upload error ({key}):", e)
            return {"status": "error", "key": key, "upload_id": upload_id}

    def generate_presigned_get_url(self, key, expires_in=3600, download_filename=None):
        try:
            params = {"Bucket": self.bucket_name, "Key": key}
            if download_filename:
                params["ResponseContentDisposition"] = f'attachment; filename="{download_filename}"'
            return self.s3_client.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=expires_in,
            )
        except Exception as e:
            print(f"Presigned URL error ({key}):", e)
            return None


    def list_user_files(self, user_id):
        prefix = self.user_uploads_prefix(user_id)
        response = self.s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=prefix)

        files = []
        for item in response.get("Contents", []):
            key = item["Key"]
            full_filename = key.split("/")[-1]

            if "_" in full_filename:
                original_name = full_filename.split("_", 1)[1]
            else:
                original_name = full_filename

            # Generate signed URL
            signed_url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': key,
                    'ResponseContentDisposition': f'attachment; filename="{original_name}"'
                },
                ExpiresIn=3600  # 1 hour
            )

            files.append({
                "key": key,
                "stored_name": full_filename,
                "original_name": original_name,
                "size_bytes": item["Size"],
                "last_modified": item["LastModified"].isoformat(),
                "url": signed_url  # Use signed URL
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
