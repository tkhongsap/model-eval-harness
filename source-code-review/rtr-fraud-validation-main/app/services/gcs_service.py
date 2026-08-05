"""GCSService — gcsfs wrapper for GCS temporary file I/O."""
from __future__ import annotations

import gcsfs

from app.core.exceptions import GCSServiceError
from app.share_log import get_logger

logger = get_logger(__name__)


class GCSService:
    """Thin wrapper around ``gcsfs.GCSFileSystem`` for the pipeline's GCS operations."""

    def __init__(self, project_id: str, bucket_name: str) -> None:
        self._project_id = project_id
        self._bucket = bucket_name
        self._fs = gcsfs.GCSFileSystem(project=project_id, token=None)

    def uri(self, relative_path: str) -> str:
        """Convert a relative path to a full ``gs://`` URI."""
        try:
            return f"gs://{self._bucket}/{relative_path}"
        except Exception as exc:
            logger.error(f"Failed to construct GCS URI: {exc}")
            raise GCSServiceError(
                f"Failed to construct GCS URI for path: {relative_path}",
                details={"relative_path": relative_path}
            ) from exc

    def exists(self, relative_path: str) -> bool:
        try:
            return self._fs.exists(self.uri(relative_path))
        except GCSServiceError:
            raise
        except Exception as exc:
            logger.error(f"Failed to check existence in GCS: {exc}")
            raise GCSServiceError(
                f"Failed to check existence in GCS: {relative_path}",
                details={"relative_path": relative_path}
            ) from exc

    def read_bytes(self, relative_path: str) -> bytes:
        try:
            with self._fs.open(self.uri(relative_path), "rb") as f:
                return f.read()
        except GCSServiceError:
            raise
        except Exception as exc:
            logger.error(f"Failed to read bytes from GCS: {exc}")
            raise GCSServiceError(
                f"Failed to read bytes from GCS: {relative_path}",
                details={"relative_path": relative_path}
            ) from exc

    def write_bytes(self, relative_path: str, data: bytes) -> None:
        try:
            self._fs.invalidate_cache(self.uri(relative_path))
            with self._fs.open(self.uri(relative_path), "wb") as f:
                f.write(data)
        except GCSServiceError:
            raise
        except Exception as exc:
            logger.error(f"Failed to write bytes to GCS: {exc}")
            raise GCSServiceError(
                f"Failed to write bytes to GCS: {relative_path}",
                details={"relative_path": relative_path, "size": len(data)}
            ) from exc

    def write_text(self, relative_path: str, text: str, encoding: str = "utf-8-sig") -> None:
        try:
            self._fs.invalidate_cache(self.uri(relative_path))
            with self._fs.open(self.uri(relative_path), "w", encoding=encoding) as f:
                f.write(text)
        except GCSServiceError:
            raise
        except Exception as exc:
            logger.error(f"Failed to write text to GCS: {exc}")
            raise GCSServiceError(
                f"Failed to write text to GCS: {relative_path}",
                details={"relative_path": relative_path, "size": len(text)}
            ) from exc

    def read_text(self, relative_path: str) -> str:
        try:
            with self._fs.open(self.uri(relative_path), "r", encoding="utf-8") as f:
                return f.read()
        except GCSServiceError:
            raise
        except Exception as exc:
            logger.error(f"Failed to read text from GCS: {exc}")
            raise GCSServiceError(
                f"Failed to read text from GCS: {relative_path}",
                details={"relative_path": relative_path}
            ) from exc

    def invalidate_cache(self, relative_path: str) -> None:
        try:
            self._fs.invalidate_cache(self.uri(relative_path))
        except GCSServiceError:
            raise
        except Exception as exc:
            logger.error(f"Failed to invalidate cache in GCS: {exc}")
            raise GCSServiceError(
                f"Failed to invalidate cache in GCS: {relative_path}",
                details={"relative_path": relative_path}
            ) from exc

    def delete(self, relative_path: str) -> None:
        try:
            self._fs.rm(self.uri(relative_path))
            logger.info(f"Deleted from GCS: {relative_path}")
        except GCSServiceError:
            raise
        except Exception as exc:
            logger.error(f"Failed to delete from GCS: {exc}")
            raise GCSServiceError(
                f"Failed to delete from GCS: {relative_path}",
                details={"relative_path": relative_path}
            ) from exc

    def copy_from_sharepoint_if_missing(
        self,
        relative_path: str,
        content: bytes,
    ) -> str:
        """Write *content* to GCS only if the file does not already exist.

        Returns the GCS URI.
        """
        try:
            uri = self.uri(relative_path)
            if not self._fs.exists(uri):
                with self._fs.open(uri, "wb") as f:
                    f.write(content)
                logger.info(f"Saved to GCS: {uri}")
            return uri
        except GCSServiceError:
            raise
        except Exception as exc:
            logger.error(f"Failed to copy to GCS: {exc}")
            raise GCSServiceError(
                f"Failed to copy to GCS: {relative_path}",
                details={"relative_path": relative_path, "size": len(content)}
            ) from exc
