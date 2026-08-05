"""Connection factories for the tax-invoice reconcile tasks.

Builds the SharePoint / GCS modules used in each task's ``pre_execute`` and the
:class:`EmailNotifier` for system-error alerts. The package is self-contained: it keeps its
own factories rather than importing them from the OCR pipeline package.
"""

from src.modules.google.gcs import GCSModule
from src.modules.microsoft.msgraph import MSGraphModule
from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.common import resolve_env
from src.utils.logger import Logger
from tasks.tax_invoice_reconcile.module import EmailNotifier

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


def init_email_notifier(framework: dict, msgraph_access: dict) -> EmailNotifier:
    """Build an :class:`EmailNotifier` from the task framework + shared Graph credentials.

    Graph credentials (``client_id``/``client_secret``/``tenant_id``) come from the shared
    ``msgraph`` block in ``common.yml``; the template directory comes from the task's own
    ``framework`` block. Per-case from/to/cc addresses are resolved by each task and passed to
    :meth:`EmailNotifier.send_template`, so the notifier itself holds no addresses.

    Args:
        framework: The task's ``framework`` config block (``email_template_dir``).
        msgraph_access: The ``msgraph`` block from ``common.yml`` (Graph credentials).

    Returns:
        A ready-to-use :class:`EmailNotifier`.
    """
    try:
        msgraph = MSGraphModule(
            client_id=resolve_env(msgraph_access.get("client_id")),
            client_secret=resolve_env(msgraph_access.get("client_secret")),
            tenant_id=resolve_env(msgraph_access.get("tenant_id")),
        )
        return EmailNotifier(msgraph=msgraph, template_dir=framework.get("email_template_dir"))
    except Exception as e:
        logger.error(f"Failed to initialize email notifier: {e}", exc_info=True)
        raise
