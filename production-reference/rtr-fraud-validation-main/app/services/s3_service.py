"""S3Service — boto3 wrapper for reading retailer shop images."""
from __future__ import annotations

import boto3
from io import BytesIO
from botocore.exceptions import ClientError
from app.core.exceptions import S3ServiceError
from app.crypto.decrypt_batchRTR import decrypt_hybrid
from app.share_log import get_logger

logger = get_logger(__name__)


class S3Service:
    """Thin, testable wrapper around boto3 S3 operations."""

    def __init__(
        self,
        aws_access_key: str,
        aws_secret_key: str,
        bucket_name: str,
    ) -> None:
        self._bucket = bucket_name
        self._client = boto3.client(
            "s3",
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
        )

    def read_bytes(self, key: str) -> bytes:
        """Download *key* from the configured bucket and return raw bytes.
        
        Raises:
            S3ServiceError: If reading fails.
        """
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()
        except self._client.exceptions.NoSuchKey:
            raise S3ServiceError(
                f"File not found for path: {key}",
                details={"key": key, "bucket": self._bucket, "reason": "NoSuchKey"}
            )
        except S3ServiceError:
            raise
        except Exception as e:
            raise S3ServiceError(
                f"Failed to read {key}: {e}",
                details={"key": key, "bucket": self._bucket}
            ) from e

    def read_bytes_encrypt(self, key: str, rsa_private_key: str) -> bytes:
        """Download *key* from the configured bucket and return raw bytes.
        
        Raises:
            S3ServiceError: If reading or decryption fails.
        """
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            content_byte = decrypt_hybrid(BytesIO(response["Body"].read()), rsa_private_key)
            return content_byte
        except self._client.exceptions.NoSuchKey:
            raise S3ServiceError(
                f"File not found for path: {key}",
                details={"key": key, "bucket": self._bucket, "reason": "NoSuchKey"}
            )
        except S3ServiceError:
            raise
        except Exception as e:
            raise S3ServiceError(
                f"Failed to read or decrypt {key}: {e}",
                details={"key": key, "bucket": self._bucket}
            ) from e
        
    def normalise_key(self, s3_path: str) -> str:
        """Strip ``s3://<bucket>/`` prefix, leaving a bare object key."""
        prefix = f"s3://{self._bucket}/"
        if s3_path.startswith(prefix):
            return s3_path[len(prefix):]
        return s3_path

    def key_exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "404":
                return False
            raise S3ServiceError(
                f"Failed to check key existence: {key}",
                details={"key": key, "bucket": self._bucket, "error": str(exc)}
            ) from exc
        except S3ServiceError:
            raise
        except Exception as exc:
            raise S3ServiceError(
                f"Failed to check key existence: {key}",
                details={"key": key, "bucket": self._bucket}
            ) from exc
