"""Connection factories for SharePoint and GCS modules used across the OCR pipeline."""

from src.modules.google.gcs import GCSModule
from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.common import resolve_env
from src.utils.logger import Logger

logger = Logger(__name__)


def init_sharepoint(label: str, access_config: dict) -> SharePointModule:
    """Initialise a SharePoint module with a labelled error message.

    ``access_config`` holds the ``client_id`` / ``client_secret`` / ``tenant_id`` /
    ``site_domain`` / ``site_path`` keys; values may be literal strings or ``${ENV_VAR}``
    placeholders resolved via :func:`resolve_env`. Extra keys are ignored. ``label`` keeps
    diagnostic context in the debug log and any error message.
    """
    try:
        site = resolve_env(access_config.get("site_domain"))
        module = SharePointModule(
            client_id=resolve_env(access_config.get("client_id")),
            client_secret=resolve_env(access_config.get("client_secret")),
            tenant_id=resolve_env(access_config.get("tenant_id")),
            site_domain=site,
            site_path=resolve_env(access_config.get("site_path")),
        )
        logger.debug(f"SharePoint {label}: {site}")
        return module
    except Exception as e:
        logger.error(f"Failed to initialize SharePoint {label} module: {e}", exc_info=True)
        raise


def init_gcs(gcs_config: dict) -> GCSModule:
    """Initialise a GCSModule from a task's ``gcs`` config block."""
    try:
        project_id = resolve_env(gcs_config.get("project_id"))
        bucket_name = resolve_env(gcs_config.get("bucket_name"))
        module = GCSModule(project_id=project_id, bucket_name=bucket_name)
        logger.debug(f"GCS: {project_id}/{bucket_name}")
        return module
    except Exception as e:
        logger.error(f"Failed to initialize GCS module: {e}", exc_info=True)
        raise
