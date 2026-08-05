import os, requests, asyncio, aiohttp
from typing import List, Dict, Any, Tuple, Optional
from google.cloud import storage
from src.log import Logger
from src.modules.sharepoint import SharePointModule

logger = Logger(__name__, env=os.environ.get("LOG_ENVIRONMENT", "prod"))

class GCSModule:
    def __init__(self, **kwargs) -> None:
        self.project_id = kwargs.get("project_id")
        self.bucket_name = kwargs.get("bucket_name")
        logger.info(f"GCSModule initialized with project_id: {self.project_id}, bucket_name: {self.bucket_name}")

    async def upload_sharepoint_to_gcs(
            self, sharepoint_object: SharePointModule, stream_list: List[Dict[Any, Any]], max_concurrent_uploads: Optional[int]=10
        ) -> None:
        """
        Upload files from SharePoint to GCS asynchronously.
        Parameters:
            sharepoint_object (SharePointModule): The SharePoint module instance
            stream_list (List[Dict[Any, Any]]): List of file streams with download and upload paths
            max_concurrent_uploads (Optional[int]): Maximum number of concurrent uploads, default is 10
        Returns:
            None
        """
        sem = asyncio.Semaphore(max_concurrent_uploads)
        gcs_client = storage.Client(project=self.project_id)
        async def processing_file(file_stream):
            try:
                async with sem:
                    loop = asyncio.get_event_loop()
                    bucket = gcs_client.bucket(self.bucket_name)
                    blob = bucket.blob(file_stream["upload"])
                    data = await loop.run_in_executor(
                        None,
                        sharepoint_object.get_item_by_path,
                        file_stream["download"]
                    )
                    await loop.run_in_executor(
                        None,
                        blob.upload_from_string,
                        data.content,
                        file_stream["mime_type"]
                    )
                    logger.info(f"Uploaded file to GCS: {file_stream['upload']}")
            except Exception as e:
                logger.error(f"Error uploading file to GCS: {e}")
        await asyncio.gather(*(processing_file(stream) for stream in stream_list))
            
    def update_content_to_gcs(
            self, content: bytes, mime_type: str, destination_path: str
        ) -> None:
        """
        Upload content to GCS.
        Parameters:
            content (bytes): The content to upload
            mime_type (str): The MIME type of the content
            destination_path (str): The destination path in the GCS bucket
        Returns:
            None
        """
        try:
            gcs_client = storage.Client(project=self.project_id)
            bucket = gcs_client.bucket(self.bucket_name)
            blob = bucket.blob(destination_path)
            blob.upload_from_string(content, content_type=mime_type)
            logger.info(f"Uploaded content to GCS: {destination_path}")
        except Exception as e:
            logger.error(f"Error uploading content to GCS: {e}")
            raise Exception(f"Error uploading content to GCS: {e}")

    def list_files(self, prefix: str="") -> List[str]:
        """
        List files in a GCS bucket with an optional prefix.
        Parameters:
            project_id (str): GCP Project ID
            bucket_name (str): GCS Bucket name
            prefix (str): The prefix to filter files
        Returns:
            List[str]: List of file paths in the GCS bucket
        """
        try:
            gcs_client = storage.Client(project=self.project_id)
            bucket = gcs_client.bucket(self.bucket_name)
            blobs = bucket.list_blobs(prefix=prefix)
            file_list = [blob.name for blob in blobs]
            logger.info(f"Listed {len(file_list)} files in GCS bucket: {self.bucket_name} with prefix: {prefix}")
            return file_list
        except Exception as e:
            logger.error(f"Error listing files in GCS: {e}")
            raise Exception(f"Error listing files in GCS: {e}")
            
    def download_file_from_gcs(self, file_path: str) -> bytes:
        """
        Download a file from GCS.
        Parameters:
            file_path (str): The path of the file in the GCS bucket
        Returns:
            bytes: The content of the downloaded file
        """
        try:
            gcs_client = storage.Client(project=self.project_id)
            bucket = gcs_client.bucket(self.bucket_name)
            blob = bucket.blob(file_path)
            content = blob.download_as_bytes()
            logger.info(f"Downloaded file from GCS: {file_path}")
            return content
        except Exception as e:
            logger.error(f"Error downloading file from GCS: {e}")
            raise Exception(f"Error downloading file from GCS: {e}")