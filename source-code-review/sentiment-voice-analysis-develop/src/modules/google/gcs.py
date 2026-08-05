# Library imports
import asyncio
from pathlib import Path
from typing import Any

from google.api_core.exceptions import PreconditionFailed
from google.cloud import storage
from google.cloud.exceptions import GoogleCloudError, NotFound

from src.modules.microsoft.sharepoint import SharePointModule

# Source code imports
from src.utils.logger import Logger

logger = Logger(__name__)


class GCSModule:
    """
    Module for interacting with Google Cloud Storage (GCS).
    Handles file uploads, downloads, and listing operations.

    TLS note:
        Unlike the Microsoft Graph and Gemini modules, no explicit minimum-TLS
        floor is pinned here. The google-cloud-storage SDK performs all I/O through
        ``google.auth.transport.requests``, which already negotiates TLS 1.2+ by
        default, and the SDK exposes no public minimum-TLS setting. The only override
        is the private ``storage.Client(_http=...)`` parameter; it is intentionally
        not used to avoid coupling to SDK internals (a fresh client is created per
        operation here, which would also make such injection fragile).
    """

    # Configuration Constants
    DEFAULT_MAX_CONCURRENT_UPLOADS = 10
    DEFAULT_MAX_CONCURRENT_COPIES = 10
    DEFAULT_MAX_CONCURRENT_DOWNLOADS = 10
    DEFAULT_PREFIX = ""

    def __init__(self, **kwargs) -> None:
        """
        Initialize GCS module with project and bucket configuration.

        Parameters:
            project_id (str): GCP project ID
            bucket_name (str): GCS bucket name

        Raises:
            ValueError: If required parameters are missing
        """
        logger.info("Initializing GCSModule...")

        self.project_id = kwargs.get("project_id")
        self.bucket_name = kwargs.get("bucket_name")

        # Validate required parameters
        if not self.project_id:
            error_msg = "project_id is required for GCSModule initialization"
            logger.error(error_msg)
            raise ValueError(error_msg)

        if not self.bucket_name:
            error_msg = "bucket_name is required for GCSModule initialization"
            logger.error(error_msg)
            raise ValueError(error_msg)

        logger.info("GCSModule initialized successfully")
        logger.info(f"Configuration - Project ID: {self.project_id}, Bucket: {self.bucket_name}")

        # Validate bucket access
        try:
            logger.debug("Validating bucket access...")
            gcs_client = storage.Client(project=self.project_id)
            bucket = gcs_client.bucket(self.bucket_name)

            # Check if bucket exists
            if bucket.exists():
                logger.debug(f"Successfully validated access to bucket: {self.bucket_name}")
            else:
                logger.warning(f"Bucket '{self.bucket_name}' may not exist or is not accessible")
        except Exception as e:
            logger.warning(f"Could not validate bucket access: {e}")
            logger.debug("Bucket validation failed, but continuing initialization")

    async def upload_sharepoint_to_gcs(
        self,
        sharepoint_object: SharePointModule,
        stream_list: list[dict[str, Any]],
        max_concurrent_uploads: int | None = None,
    ) -> dict[str, Any]:
        """
        Upload files from SharePoint to GCS asynchronously with concurrency control.

        Parameters:
            sharepoint_object (SharePointModule): The SharePoint module instance
            stream_list (List[Dict[str, Any]]): List of file streams with 'download', 'upload', and 'mime_type' keys
            max_concurrent_uploads (Optional[int]): Maximum concurrent uploads. Defaults to 10

        Returns:
            Dict[str, Any]: Summary of upload results with success and failure counts

        Raises:
            Exception: If critical errors occur during upload process
        """
        if max_concurrent_uploads is None:
            max_concurrent_uploads = self.DEFAULT_MAX_CONCURRENT_UPLOADS

        total_files = len(stream_list)
        logger.info(f"Starting async upload of {total_files} file(s) from SharePoint to GCS")
        logger.debug(f"Max concurrent uploads: {max_concurrent_uploads}")

        if total_files == 0:
            logger.warning("No files to upload")
            return {"total": 0, "success": 0, "failed": 0, "errors": []}

        sem = asyncio.Semaphore(max_concurrent_uploads)
        gcs_client = storage.Client(project=self.project_id)
        bucket = gcs_client.bucket(self.bucket_name)

        success_count = 0
        failed_count = 0
        errors = []

        async def processing_file(file_index: int, file_stream: dict[str, Any]):
            """Process a single file upload."""
            nonlocal success_count, failed_count

            download_path = file_stream.get("download")
            upload_path = file_stream.get("upload")
            mime_type = file_stream.get("mime_type")

            if not all([download_path, upload_path, mime_type]):
                error_msg = f"Missing required keys in file_stream at index {file_index}: {file_stream.keys()}"
                logger.error(error_msg)
                failed_count += 1
                errors.append({"index": file_index, "error": error_msg})
                return

            try:
                async with sem:
                    logger.debug(f"[{file_index + 1}/{total_files}] Processing: {download_path}")

                    loop = asyncio.get_event_loop()
                    blob = bucket.blob(upload_path)

                    # Download from SharePoint
                    logger.debug(f"[{file_index + 1}/{total_files}] Downloading from SharePoint: {download_path}")
                    data = await loop.run_in_executor(None, sharepoint_object.get_item_by_path, download_path)

                    file_size_kb = len(data.content) / 1024
                    logger.debug(f"[{file_index + 1}/{total_files}] Downloaded {file_size_kb:.2f} KB")

                    # Upload to GCS
                    logger.debug(f"[{file_index + 1}/{total_files}] Uploading to GCS: {upload_path}")
                    await loop.run_in_executor(None, blob.upload_from_string, data.content, mime_type)

                    success_count += 1
                    logger.info(
                        f"[{file_index + 1}/{total_files}] Successfully uploaded: {upload_path} ({file_size_kb:.2f} KB)"
                    )

            except Exception as e:
                failed_count += 1
                error_msg = f"Failed to upload '{download_path}': {e}"
                logger.error(f"[{file_index + 1}/{total_files}] {error_msg}", exc_info=True)
                errors.append(
                    {"index": file_index, "download_path": download_path, "upload_path": upload_path, "error": str(e)}
                )

        # Process all files concurrently
        await asyncio.gather(*(processing_file(idx, stream) for idx, stream in enumerate(stream_list)))

        # Summary
        logger.info(f"Upload complete: {success_count} succeeded, {failed_count} failed out of {total_files} total")

        if failed_count > 0:
            logger.warning(f"{failed_count} file(s) failed to upload")

        return {"total": total_files, "success": success_count, "failed": failed_count, "errors": errors}

    def update_content_to_gcs(
        self, content: bytes, mime_type: str, destination_path: str, if_generation_match: int | None = None
    ) -> None:
        """
        Upload content (bytes) to GCS.

        Parameters:
            content (bytes): The binary content to upload
            mime_type (str): The MIME type of the content (e.g., 'application/json', 'text/plain')
            destination_path (str): The destination path in the GCS bucket
            if_generation_match (int | None): Optimistic-concurrency precondition. When set, the
                upload only succeeds if the object's current generation matches (``0`` means the
                object must not yet exist); a mismatch raises
                ``google.api_core.exceptions.PreconditionFailed``. ``None`` (default) uploads
                unconditionally — the original behavior.

        Raises:
            PreconditionFailed: If ``if_generation_match`` is set and the object's generation
                does not match (propagated unwrapped so callers can reload and retry).
            Exception: If upload fails for any other reason.
        """
        content_size_kb = len(content) / 1024
        logger.info(f"Uploading {content_size_kb:.2f} KB to GCS: {destination_path}")
        logger.debug(f"MIME type: {mime_type}")

        try:
            gcs_client = storage.Client(project=self.project_id)
            bucket = gcs_client.bucket(self.bucket_name)
            blob = bucket.blob(destination_path)

            logger.debug(f"Uploading to gs://{self.bucket_name}/{destination_path}")
            blob.upload_from_string(content, content_type=mime_type, if_generation_match=if_generation_match)

            logger.info(f"Successfully uploaded content to GCS: {destination_path} ({content_size_kb:.2f} KB)")

        except PreconditionFailed:
            # Generation mismatch — propagate unwrapped so callers can reload + retry. Must come
            # before GoogleCloudError, which is a superclass of PreconditionFailed.
            logger.warning(f"Generation precondition failed for {destination_path} (concurrent write)")
            raise
        except NotFound as e:
            logger.error(f"Bucket or path not found: {e}")
            raise Exception(f"Bucket '{self.bucket_name}' not found: {e}") from e
        except GoogleCloudError as e:
            logger.error(f"GCS error uploading content: {e}", exc_info=True)
            raise Exception(f"GCS error uploading content to {destination_path}: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error uploading content to GCS: {e}", exc_info=True)
            raise Exception(f"Error uploading content to GCS: {e}") from e

    def list_directories(self, prefix: str | Path = None, delimiter: str = "/") -> list[str]:
        """
        List "directories" in the GCS bucket using a delimiter.

        Parameters:
            prefix (str | Path, optional): Filter directories by prefix. Defaults to empty string (all directories)
            delimiter (str, optional): Delimiter to simulate directory structure. Defaults to "/"

        Returns:
            List[str]: List of directory paths in the GCS bucket

        Raises:
            Exception: If listing fails
        """
        if prefix is None:
            prefix = self.DEFAULT_PREFIX

        if isinstance(prefix, Path):
            prefix = str(prefix).replace("\\", "/")

        logger.info(
            f"Listing directories in bucket '{self.bucket_name}' with prefix: '{prefix}' and delimiter: '{delimiter}'"
        )

        try:
            gcs_client = storage.Client(project=self.project_id)
            bucket = gcs_client.bucket(self.bucket_name)

            logger.debug(f"Fetching blobs from gs://{self.bucket_name}/{prefix} with delimiter '{delimiter}'")
            blobs = bucket.list_blobs(prefix=prefix, delimiter=delimiter)

            directories = blobs.prefixes

            logger.info(f"Found {len(directories)} directorie(s) in bucket '{self.bucket_name}' with prefix '{prefix}'")

            if len(directories) > 0:
                logger.debug(f"Directories: {list(directories)[:5]}")

            return list(directories)

        except NotFound as e:
            logger.error(f"Bucket not found: {e}")
            raise Exception(f"Bucket '{self.bucket_name}' not found: {e}") from e
        except GoogleCloudError as e:
            logger.error(f"GCS error listing directories: {e}", exc_info=True)
            raise Exception(f"GCS error listing directories in bucket '{self.bucket_name}': {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error listing directories in GCS: {e}", exc_info=True)
            raise Exception(f"Error listing directories in GCS: {e}") from e

    def list_files(self, prefix: str | Path = None) -> list[str]:
        """
        List files in the GCS bucket with an optional prefix filter.

        Parameters:
            prefix (str | Path, optional): Filter files by prefix. Defaults to empty string (all files)

        Returns:
            List[str]: List of file paths in the GCS bucket

        Raises:
            Exception: If listing fails
        """
        # Handle Path objects
        if isinstance(prefix, Path):
            prefix = str(prefix).replace("\\", "/")
        elif prefix is None:
            prefix = self.DEFAULT_PREFIX

        logger.info(f"Listing files in bucket '{self.bucket_name}' with prefix: '{prefix}'")

        try:
            gcs_client = storage.Client(project=self.project_id)
            bucket = gcs_client.bucket(self.bucket_name)

            logger.debug(f"Fetching blobs from gs://{self.bucket_name}/{prefix}")
            blobs = bucket.list_blobs(prefix=prefix)

            # Filter directories
            file_list = [blob.name for blob in blobs if not blob.name.endswith("/")]

            logger.info(f"Found {len(file_list)} file(s) in bucket '{self.bucket_name}' with prefix '{prefix}'")

            if len(file_list) > 0:
                logger.debug(f"First few files: {file_list[:5]}")
            else:
                logger.debug("No files found matching criteria")

            return file_list

        except NotFound as e:
            logger.error(f"Bucket not found: {e}")
            raise Exception(f"Bucket '{self.bucket_name}' not found: {e}") from e
        except GoogleCloudError as e:
            logger.error(f"GCS error listing files: {e}", exc_info=True)
            raise Exception(f"GCS error listing files in bucket '{self.bucket_name}': {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error listing files in GCS: {e}", exc_info=True)
            raise Exception(f"Error listing files in GCS: {e}") from e

    def download_file_from_gcs(self, file_path: str) -> bytes:
        """
        Download a file from GCS and return its content as bytes.

        Parameters:
            file_path (str): The path of the file in the GCS bucket

        Returns:
            bytes: The binary content of the downloaded file

        Raises:
            Exception: If download fails or file not found
        """
        logger.info(f"Downloading file from GCS: {file_path}")
        logger.debug(f"Source: gs://{self.bucket_name}/{file_path}")

        try:
            if file_path.startswith("gs://"):
                file_path = file_path[len("gs://") :]
                if file_path.startswith(f"{self.bucket_name}/"):
                    file_path = file_path[len(f"{self.bucket_name}/") :]
                else:
                    error_msg = f"File path '{file_path}' does not match bucket '{self.bucket_name}'"
                    logger.error(error_msg)
                    raise FileNotFoundError(error_msg)
            gcs_client = storage.Client(project=self.project_id)
            bucket = gcs_client.bucket(self.bucket_name)
            blob = bucket.blob(file_path)

            # Check if blob exists
            if not blob.exists():
                error_msg = f"File not found in GCS: gs://{self.bucket_name}/{file_path}"
                logger.error(error_msg)
                raise FileNotFoundError(error_msg)

            logger.debug("Downloading blob...")
            content = blob.download_as_bytes()

            file_size_kb = len(content) / 1024
            logger.info(f"Successfully downloaded file from GCS: {file_path} ({file_size_kb:.2f} KB)")

            return content

        except FileNotFoundError:
            raise
        except NotFound as e:
            error_msg = f"File or bucket not found: gs://{self.bucket_name}/{file_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg) from e
        except GoogleCloudError as e:
            logger.error(f"GCS error downloading file: {e}", exc_info=True)
            raise Exception(f"GCS error downloading file '{file_path}': {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error downloading file from GCS: {e}", exc_info=True)
            raise Exception(f"Error downloading file from GCS: {e}") from e

    def download_bytes_with_generation(self, file_path: str) -> tuple[bytes, int | None]:
        """
        Download a file's bytes together with its GCS object generation.

        The generation enables optimistic-concurrency writes: pass it back as
        ``if_generation_match`` to :meth:`update_content_to_gcs` to detect a competing write.

        Parameters:
            file_path (str): The path of the file in the GCS bucket (``gs://...`` or relative).

        Returns:
            tuple[bytes, int | None]: The content and the object's current generation.

        Raises:
            FileNotFoundError: If the object does not exist.
            Exception: If the download fails for any other reason.
        """
        if file_path.startswith("gs://"):
            file_path = file_path[len("gs://") :]
            if file_path.startswith(f"{self.bucket_name}/"):
                file_path = file_path[len(f"{self.bucket_name}/") :]
            else:
                error_msg = f"File path '{file_path}' does not match bucket '{self.bucket_name}'"
                logger.error(error_msg)
                raise FileNotFoundError(error_msg)

        try:
            gcs_client = storage.Client(project=self.project_id)
            bucket = gcs_client.bucket(self.bucket_name)
            # get_blob does a metadata GET that populates ``generation`` (None when absent).
            blob = bucket.get_blob(file_path)
            if blob is None:
                error_msg = f"File not found in GCS: gs://{self.bucket_name}/{file_path}"
                logger.error(error_msg)
                raise FileNotFoundError(error_msg)
            content = blob.download_as_bytes()
            return content, blob.generation
        except FileNotFoundError:
            raise
        except NotFound as e:
            error_msg = f"File or bucket not found: gs://{self.bucket_name}/{file_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg) from e
        except GoogleCloudError as e:
            logger.error(f"GCS error downloading file with generation: {e}", exc_info=True)
            raise Exception(f"GCS error downloading file '{file_path}': {e}") from e

    def move_file(self, source_path: str, destination_path: str) -> None:
        """
        Move (rename) a file within the GCS bucket.

        Parameters:
            source_path (str): The current path of the file in GCS
            destination_path (str): The new path for the file in GCS

        Raises:
            Exception: If move operation fails
        """
        logger.info(f"Moving file in GCS from '{source_path}' to '{destination_path}'")

        try:
            gcs_client = storage.Client(project=self.project_id)
            bucket = gcs_client.bucket(self.bucket_name)
            source_blob = bucket.blob(source_path)

            # Check if source blob exists
            if not source_blob.exists():
                error_msg = f"Source file not found in GCS: gs://{self.bucket_name}/{source_path}"
                logger.error(error_msg)
                raise FileNotFoundError(error_msg)

            # Perform the copy and delete to simulate move
            bucket.copy_blob(source_blob, bucket, destination_path)
            source_blob.delete()

            logger.info(f"Successfully moved file to '{destination_path}'")

        except FileNotFoundError:
            raise
        except NotFound as e:
            error_msg = f"File or bucket not found during move: gs://{self.bucket_name}/{source_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg) from e
        except GoogleCloudError as e:
            logger.error(f"GCS error moving file: {e}", exc_info=True)
            raise Exception(f"GCS error moving file from '{source_path}' to '{destination_path}': {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error moving file in GCS: {e}", exc_info=True)
            raise Exception(f"Error moving file in GCS: {e}") from e

    def delete_file(self, file_path: str) -> None:
        """
        Delete a file from the GCS bucket.

        Parameters:
            file_path (str): The path of the file in GCS to delete
        Raises:
            Exception: If delete operation fails
        """
        logger.info(f"Deleting file from GCS: {file_path}")

        try:
            gcs_client = storage.Client(project=self.project_id)
            bucket = gcs_client.bucket(self.bucket_name)
            blob = bucket.blob(file_path)

            # Check if blob exists
            if not blob.exists():
                error_msg = f"File not found in GCS: gs://{self.bucket_name}/{file_path}"
                logger.error(error_msg)
                raise FileNotFoundError(error_msg)

            blob.delete()
            logger.info(f"Successfully deleted file from GCS: {file_path}")

        except FileNotFoundError:
            raise
        except NotFound as e:
            error_msg = f"File or bucket not found during delete: gs://{self.bucket_name}/{file_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg) from e
        except GoogleCloudError as e:
            logger.error(f"GCS error deleting file: {e}", exc_info=True)
            raise Exception(f"GCS error deleting file '{file_path}': {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error deleting file from GCS: {e}", exc_info=True)
            raise Exception(f"Error deleting file from GCS: {e}") from e

    def is_file_exists(self, file_path: str) -> bool:
        """
        Check if a file exists in the GCS bucket.

        Parameters:
            file_path (str): The path of the file in GCS to check
        Returns:
            bool: True if the file exists, False otherwise
        """
        logger.info(f"Checking if file exists in GCS: {file_path}")

        try:
            gcs_client = storage.Client(project=self.project_id)
            bucket = gcs_client.bucket(self.bucket_name)
            blob = bucket.blob(file_path)

            if blob.exists():
                logger.info(f"File exists in GCS: {file_path}")
                return True
            logger.info(f"File does not exist in GCS: {file_path}")
            return False

        except NotFound as e:
            logger.error(f"Bucket not found while checking file: {e}")
            raise Exception(f"Bucket '{self.bucket_name}' not found: {e}") from e
        except GoogleCloudError as e:
            logger.error(f"GCS error checking file existence: {e}", exc_info=True)
            raise Exception(f"GCS error checking file '{file_path}': {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error checking file in GCS: {e}", exc_info=True)
            raise Exception(f"Error checking file in GCS: {e}") from e

    def is_dir_exists(self, dir_path: str) -> bool:
        """
        Check if a "directory" (prefix) exists in the GCS bucket.

        Parameters:
            dir_path (str): The directory path (prefix) to check in GCS
        Returns:
            bool: True if the directory exists (has files), False otherwise
        """
        logger.info(f"Checking if directory exists in GCS: {dir_path}")

        try:
            gcs_client = storage.Client(project=self.project_id)
            bucket = gcs_client.bucket(self.bucket_name)

            blobs = bucket.list_blobs(prefix=dir_path)
            for _ in blobs:
                logger.info(f"Directory exists in GCS: {dir_path}")
                return True

            logger.info(f"Directory does not exist in GCS: {dir_path}")
            return False

        except NotFound as e:
            logger.error(f"Bucket not found while checking directory: {e}")
            raise Exception(f"Bucket '{self.bucket_name}' not found: {e}") from e
        except GoogleCloudError as e:
            logger.error(f"GCS error checking directory existence: {e}", exc_info=True)
            raise Exception(f"GCS error checking directory '{dir_path}': {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error checking directory in GCS: {e}", exc_info=True)
            raise Exception(f"Error checking directory in GCS: {e}") from e

    def cleanup_empty_dir_markers(self, prefix: str | Path = None) -> int:
        """
        Clean up empty directory marker objects (0-byte blobs ending with '/').
        These are often created by tools to represent directory structures.

        Parameters:
            prefix (str, optional): Only clean up markers under this prefix. Defaults to entire bucket

        Returns:
            int: Number of directory markers deleted

        Raises:
            Exception: If cleanup operation fails
        """
        logger.info(f"Cleaning up empty directory markers in GCS with prefix: '{prefix}'")

        try:
            if prefix is None:
                prefix = self.DEFAULT_PREFIX
            elif isinstance(prefix, Path):
                prefix = str(prefix).replace("\\", "/")

            gcs_client = storage.Client(project=self.project_id)
            bucket = gcs_client.bucket(self.bucket_name)

            blobs = bucket.list_blobs(prefix=prefix)
            deleted_count = 0

            for blob in blobs:
                # Check if it's a directory marker (ends with '/' and has 0 bytes)
                if blob.name.endswith("/") and blob.size == 0:
                    logger.debug(f"Deleting directory marker: {blob.name}")
                    blob.delete()
                    deleted_count += 1

            if deleted_count > 0:
                logger.info(f"Successfully deleted {deleted_count} empty directory marker(s)")
            else:
                logger.info("No empty directory markers found")

            return deleted_count

        except NotFound as e:
            logger.error(f"Bucket not found during cleanup: {e}")
            raise Exception(f"Bucket '{self.bucket_name}' not found: {e}") from e
        except GoogleCloudError as e:
            logger.error(f"GCS error cleaning up directory markers: {e}", exc_info=True)
            raise Exception(f"GCS error cleaning up directory markers: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error cleaning up directory markers in GCS: {e}", exc_info=True)
            raise Exception(f"Error cleaning up directory markers in GCS: {e}") from e

    def delete_dir(self, dir_path: str) -> int:
        """
        Delete a directory and all its contents, including directory markers.

        Parameters:
            dir_path (str): The directory path to delete in GCS

        Returns:
            int: Number of blobs deleted (including directory markers)

        Raises:
            Exception: If delete operation fails
        """
        logger.info(f"Deleting directory and all contents from GCS: {dir_path}")

        # Ensure dir_path ends with / for proper prefix matching
        if not dir_path.endswith("/"):
            dir_path = f"{dir_path}/"

        try:
            gcs_client = storage.Client(project=self.project_id)
            bucket = gcs_client.bucket(self.bucket_name)

            blobs = bucket.list_blobs(prefix=dir_path)
            deleted_count = 0

            for blob in blobs:
                logger.debug(f"Deleting: {blob.name} ({blob.size} bytes)")
                blob.delete()
                deleted_count += 1

            if deleted_count > 0:
                logger.info(f"Successfully deleted {deleted_count} blob(s) from directory: {dir_path}")
            else:
                logger.warning(f"No blobs found in directory: {dir_path}")

            return deleted_count

        except NotFound as e:
            logger.error(f"Bucket not found during directory delete: {e}")
            raise Exception(f"Bucket '{self.bucket_name}' not found: {e}") from e
        except GoogleCloudError as e:
            logger.error(f"GCS error deleting directory: {e}", exc_info=True)
            raise Exception(f"GCS error deleting directory '{dir_path}': {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error deleting directory from GCS: {e}", exc_info=True)
            raise Exception(f"Error deleting directory from GCS: {e}") from e

    def copy_file(self, source_path: str, destination_path: str) -> None:
        """
        Copy a file within the GCS bucket.

        Parameters:
            source_path (str): The current path of the file in GCS
            destination_path (str): The new path for the copied file in GCS

        Raises:
            Exception: If copy operation fails
        """
        logger.info(f"Copying file in GCS from '{source_path}' to '{destination_path}'")

        try:
            gcs_client = storage.Client(project=self.project_id)
            bucket = gcs_client.bucket(self.bucket_name)
            source_blob = bucket.blob(source_path)

            # Check if source blob exists
            if not source_blob.exists():
                error_msg = f"Source file not found in GCS: gs://{self.bucket_name}/{source_path}"
                logger.error(error_msg)
                raise FileNotFoundError(error_msg)

            # Perform the copy
            bucket.copy_blob(source_blob, bucket, destination_path)

            logger.info(f"Successfully copied file to '{destination_path}'")

        except FileNotFoundError:
            raise
        except NotFound as e:
            error_msg = f"File or bucket not found during copy: gs://{self.bucket_name}/{source_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg) from e
        except GoogleCloudError as e:
            logger.error(f"GCS error copying file: {e}", exc_info=True)
            raise Exception(f"GCS error copying file from '{source_path}' to '{destination_path}': {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error copying file in GCS: {e}", exc_info=True)
            raise Exception(f"Error copying file in GCS: {e}") from e

    async def copy_files_batch(
        self, source_files: list[str], destination_prefix: str, max_concurrent_copies: int | None = None
    ) -> dict[str, Any]:
        """
        Copy multiple files within the GCS bucket in parallel with concurrency control.

        Parameters:
            source_files (List[str]): List of source file paths in GCS
            destination_prefix (str): Destination folder prefix (files will keep their names)
            max_concurrent_copies (Optional[int]): Maximum concurrent copies. Defaults to 10

        Returns:
            Dict[str, Any]: Summary with total, success, and failed counts

        Raises:
            Exception: If critical errors occur during copy process
        """
        if max_concurrent_copies is None:
            max_concurrent_copies = self.DEFAULT_MAX_CONCURRENT_COPIES

        total_files = len(source_files)
        logger.info(f"Copying {total_files} file(s) in parallel to '{destination_prefix}'")
        logger.debug(f"Max concurrent copies: {max_concurrent_copies}")

        if total_files == 0:
            logger.warning("No files to copy")
            return {"total": 0, "success": 0, "failed": 0, "errors": []}

        success_count = 0
        failed_count = 0
        errors = []

        gcs_client = storage.Client(project=self.project_id)
        bucket = gcs_client.bucket(self.bucket_name)
        sem = asyncio.Semaphore(max_concurrent_copies)

        async def copy_single_file(file_index: int, source_path: str):
            """Copy a single file."""
            nonlocal success_count, failed_count

            file_name = source_path.split("/")[-1]
            destination_path = f"{destination_prefix}/{file_name}"

            try:
                async with sem:
                    logger.debug(f"[{file_index + 1}/{total_files}] Copying '{source_path}' to '{destination_path}'")

                    loop = asyncio.get_event_loop()
                    source_blob = bucket.blob(source_path)

                    # Check if source exists
                    exists = await loop.run_in_executor(None, source_blob.exists)
                    if not exists:
                        error_msg = f"Source file not found: {source_path}"
                        logger.error(f"[{file_index + 1}/{total_files}] {error_msg}")
                        failed_count += 1
                        errors.append({"index": file_index, "source": source_path, "error": error_msg})
                        return

                    # Perform copy
                    await loop.run_in_executor(None, bucket.copy_blob, source_blob, bucket, destination_path)

                    success_count += 1
                    logger.info(f"[{file_index + 1}/{total_files}] Successfully copied to '{destination_path}'")

            except Exception as e:
                failed_count += 1
                error_msg = f"Failed to copy '{source_path}': {e}"
                logger.error(f"[{file_index + 1}/{total_files}] {error_msg}", exc_info=True)
                errors.append(
                    {"index": file_index, "source": source_path, "destination": destination_path, "error": str(e)}
                )

        await asyncio.gather(*[copy_single_file(idx, source) for idx, source in enumerate(source_files)])

        logger.info(f"Copy complete: {success_count} succeeded, {failed_count} failed out of {total_files} total")

        if failed_count > 0:
            logger.warning(f"{failed_count} file(s) failed to copy")

        return {"total": total_files, "success": success_count, "failed": failed_count, "errors": errors}

    async def upload_contents_batch(
        self, contents: list[dict[str, Any]], max_concurrent_uploads: int | None = None
    ) -> dict[str, Any]:
        """
        Upload multiple contents (bytes) to GCS in parallel with concurrency control.

        Parameters:
            contents (List[Dict[str, Any]]): List of dicts with 'content', 'mime_type', and 'destination_path' keys
            max_concurrent_uploads (Optional[int]): Maximum concurrent uploads. Defaults to 10

        Returns:
            Dict[str, Any]: Summary with the keys:
                - total (int): number of items received
                - success (int): number of items uploaded successfully
                - failed (int): number of items that did not upload
                - failed_files (List[Optional[str]]): destination paths of items that failed
                  (entry is None when the item was rejected before a destination could be determined)
                - errors (List[Dict[str, Any]]): per-item error detail

        Raises:
            Exception: If critical errors occur during upload process
        """
        if max_concurrent_uploads is None:
            max_concurrent_uploads = self.DEFAULT_MAX_CONCURRENT_UPLOADS

        total_items = len(contents)
        logger.info(f"Uploading {total_items} content item(s) to GCS in parallel")
        logger.debug(f"Max concurrent uploads: {max_concurrent_uploads}")

        if total_items == 0:
            logger.warning("No content items to upload")
            return {"total": 0, "success": 0, "failed": 0, "failed_files": [], "errors": []}

        success_count = 0
        failed_count = 0
        errors: list[dict[str, Any]] = []
        failed_files: list[str | None] = []

        gcs_client = storage.Client(project=self.project_id)
        bucket = gcs_client.bucket(self.bucket_name)
        sem = asyncio.Semaphore(max_concurrent_uploads)

        async def upload_single_content(index: int, item: dict[str, Any]):
            """Upload a single content item."""
            nonlocal success_count, failed_count

            content = item.get("content")
            mime_type = item.get("mime_type")
            destination_path = item.get("destination_path")

            if content is None or not mime_type or not destination_path:
                error_msg = f"Missing required keys in content item at index {index}: {list(item.keys())}"
                logger.error(error_msg)
                failed_count += 1
                errors.append({"index": index, "destination": destination_path, "error": error_msg})
                failed_files.append(destination_path)
                return

            content_size_kb = len(content) / 1024

            try:
                async with sem:
                    logger.debug(
                        f"[{index + 1}/{total_items}] Uploading content to '{destination_path}' ({content_size_kb:.2f} KB)"  # noqa: E501
                    )
                    blob = bucket.blob(destination_path)
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        blob.upload_from_string,
                        content,
                        mime_type,
                    )
                    success_count += 1
                    logger.info(
                        f"[{index + 1}/{total_items}] Successfully uploaded content to '{destination_path}' ({content_size_kb:.2f} KB)"  # noqa: E501
                    )
            except Exception as e:
                failed_count += 1
                error_msg = f"Failed to upload content to '{destination_path}': {e}"
                logger.error(f"[{index + 1}/{total_items}] {error_msg}", exc_info=True)
                errors.append({"index": index, "destination": destination_path, "error": str(e)})
                failed_files.append(destination_path)

        await asyncio.gather(*(upload_single_content(idx, item) for idx, item in enumerate(contents)))

        logger.info(f"Upload complete: {success_count} succeeded, {failed_count} failed out of {total_items} total")

        if failed_count > 0:
            logger.warning(f"{failed_count} content item(s) failed to upload: {[f for f in failed_files if f]}")

        return {
            "total": total_items,
            "success": success_count,
            "failed": failed_count,
            "failed_files": failed_files,
            "errors": errors,
        }

    async def download_files_batch(
        self, file_paths: list[str], max_concurrent_downloads: int | None = None
    ) -> dict[str, Any]:
        """
        Download multiple files from GCS in parallel with concurrency control.

        Files are downloaded into memory and returned as bytes.

        Parameters:
            file_paths (List[str]): List of source file paths in the GCS bucket
            max_concurrent_downloads (Optional[int]): Maximum concurrent downloads.
                Defaults to self.DEFAULT_MAX_CONCURRENT_DOWNLOADS.

        Returns:
            Dict[str, Any]: Summary with the keys:
                - total (int): number of paths received
                - success (int): number of files downloaded
                - failed (int): number of files that did not download
                - files (Dict[str, bytes]): source_path -> content (successes only)
                - failed_files (List[str]): source paths that did not download
                - errors (List[Dict[str, Any]]): per-item error detail
        """
        if max_concurrent_downloads is None:
            max_concurrent_downloads = self.DEFAULT_MAX_CONCURRENT_DOWNLOADS

        total_files = len(file_paths)
        logger.info(f"Downloading {total_files} file(s) from GCS in parallel")
        logger.debug(f"Max concurrent downloads: {max_concurrent_downloads}")

        if total_files == 0:
            logger.warning("No files to download")
            return {"total": 0, "success": 0, "failed": 0, "files": {}, "failed_files": [], "errors": []}

        success_count = 0
        failed_count = 0
        files: dict[str, bytes] = {}
        failed_files: list[str] = []
        errors: list[dict[str, Any]] = []

        gcs_client = storage.Client(project=self.project_id)
        bucket = gcs_client.bucket(self.bucket_name)
        sem = asyncio.Semaphore(max_concurrent_downloads)

        async def download_single_file(index: int, source_path: str):
            """Download a single file."""
            nonlocal success_count, failed_count

            if not source_path:
                error_msg = f"Empty source path at index {index}"
                logger.error(error_msg)
                failed_count += 1
                errors.append({"index": index, "source": source_path, "error": error_msg})
                failed_files.append(source_path)
                return

            try:
                async with sem:
                    logger.debug(f"[{index + 1}/{total_files}] Downloading '{source_path}'")
                    blob = bucket.blob(source_path)
                    loop = asyncio.get_running_loop()

                    exists = await loop.run_in_executor(None, blob.exists)
                    if not exists:
                        error_msg = f"Source file not found: {source_path}"
                        logger.error(f"[{index + 1}/{total_files}] {error_msg}")
                        failed_count += 1
                        errors.append({"index": index, "source": source_path, "error": error_msg})
                        failed_files.append(source_path)
                        return

                    content = await loop.run_in_executor(None, blob.download_as_bytes)
                    files[source_path] = content
                    success_count += 1
                    file_size_kb = len(content) / 1024
                    logger.info(
                        f"[{index + 1}/{total_files}] Successfully downloaded '{source_path}' ({file_size_kb:.2f} KB)"
                    )
            except Exception as e:
                failed_count += 1
                error_msg = f"Failed to download '{source_path}': {e}"
                logger.error(f"[{index + 1}/{total_files}] {error_msg}", exc_info=True)
                errors.append({"index": index, "source": source_path, "error": str(e)})
                failed_files.append(source_path)

        await asyncio.gather(*(download_single_file(idx, path) for idx, path in enumerate(file_paths)))

        logger.info(f"Download complete: {success_count} succeeded, {failed_count} failed out of {total_files} total")

        if failed_count > 0:
            logger.warning(f"{failed_count} file(s) failed to download: {failed_files}")

        return {
            "total": total_files,
            "success": success_count,
            "failed": failed_count,
            "files": files,
            "failed_files": failed_files,
            "errors": errors,
        }

    async def delete_files_batch(
        self,
        file_paths: list[str],
        max_concurrent_deletes: int | None = None,
    ) -> dict[str, Any]:
        """
        Delete multiple files from GCS in parallel with concurrency control.

        Parameters:
            file_paths (List[str]): List of file paths in the GCS bucket to delete
            max_concurrent_deletes (Optional[int]): Maximum concurrent deletes.
                Defaults to self.DEFAULT_MAX_CONCURRENT_UPLOADS.

        Returns:
            Dict[str, Any]: Summary with the keys:
                - total (int): number of paths received
                - success (int): number of files deleted
                - failed (int): number of files that did not delete
                - failed_files (List[str]): paths that did not delete
                - errors (List[Dict[str, Any]]): per-item error detail
        """
        if max_concurrent_deletes is None:
            max_concurrent_deletes = self.DEFAULT_MAX_CONCURRENT_UPLOADS

        total_files = len(file_paths)
        logger.info(f"Deleting {total_files} file(s) from GCS in parallel")
        logger.debug(f"Max concurrent deletes: {max_concurrent_deletes}")

        if total_files == 0:
            logger.warning("No files to delete")
            return {"total": 0, "success": 0, "failed": 0, "failed_files": [], "errors": []}

        success_count = 0
        failed_count = 0
        failed_files: list[str] = []
        errors: list[dict[str, Any]] = []

        gcs_client = storage.Client(project=self.project_id)
        bucket = gcs_client.bucket(self.bucket_name)
        sem = asyncio.Semaphore(max_concurrent_deletes)

        async def delete_single_file(index: int, file_path: str):
            """Delete a single file."""
            nonlocal success_count, failed_count

            if not file_path:
                error_msg = f"Empty file path at index {index}"
                logger.error(error_msg)
                failed_count += 1
                errors.append({"index": index, "path": file_path, "error": error_msg})
                failed_files.append(file_path)
                return

            try:
                async with sem:
                    logger.debug(f"[{index + 1}/{total_files}] Deleting '{file_path}'")
                    blob = bucket.blob(file_path)
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, blob.delete)
                    success_count += 1
                    logger.info(f"[{index + 1}/{total_files}] Successfully deleted '{file_path}'")
            except NotFound:
                failed_count += 1
                error_msg = f"File not found: {file_path}"
                logger.warning(f"[{index + 1}/{total_files}] {error_msg}")
                errors.append({"index": index, "path": file_path, "error": error_msg})
                failed_files.append(file_path)
            except Exception as e:
                failed_count += 1
                error_msg = f"Failed to delete '{file_path}': {e}"
                logger.error(f"[{index + 1}/{total_files}] {error_msg}", exc_info=True)
                errors.append({"index": index, "path": file_path, "error": str(e)})
                failed_files.append(file_path)

        await asyncio.gather(*(delete_single_file(idx, path) for idx, path in enumerate(file_paths)))

        logger.info(f"Delete complete: {success_count} succeeded, {failed_count} failed out of {total_files} total")

        if failed_count > 0:
            logger.warning(f"{failed_count} file(s) failed to delete: {failed_files}")

        return {
            "total": total_files,
            "success": success_count,
            "failed": failed_count,
            "failed_files": failed_files,
            "errors": errors,
        }
