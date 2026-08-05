"""GCS path resolution and per-bucket module routing for the OCR pipeline."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from src.modules.google.gcs import GCSModule
from src.utils.common import resolve_date, resolve_env
from tasks.ocr_tax_invoice_pipeline.helper.init_conn import init_gcs


class GcsRouter:
    """Resolves ``gcs.*`` config paths and routes operations to per-bucket GCS modules.

    Resolution order is load-bearing: ``${JOB_ID}`` is substituted FIRST (it matches the
    env-var pattern and would otherwise be blanked by :func:`resolve_env`), then
    ``${ENV_VAR}`` via :func:`resolve_env`, then ``%{DATA_DATE...}`` via :func:`resolve_date`.

    Each ``gcs.*`` path may name a different bucket within the same ``gcs.project_id``; one
    :class:`GCSModule` is cached per distinct bucket.
    """

    def __init__(
        self,
        gcs_config: dict[str, Any],
        job_id: str,
        execution_dt: datetime,
        gcs_factory: Callable[[dict[str, Any]], GCSModule] = init_gcs,
    ) -> None:
        """Initialise the router.

        Args:
            gcs_config: The task's ``gcs`` config block (raw, unresolved placeholders).
            job_id: The ``job_id`` package value, substituted for ``${JOB_ID}``.
            execution_dt: Default base date for ``%{DATA_DATE...}`` resolution.
            gcs_factory: Callable building a :class:`GCSModule` from a ``{project_id,
                bucket_name}`` dict. Injected so tests can pass a fake.
        """
        self._gcs_config = gcs_config
        self._job_id = job_id
        self._execution_dt = execution_dt
        self._gcs_factory = gcs_factory
        self._project_id = resolve_env(gcs_config.get("project_id", ""))
        self._by_bucket: dict[str, GCSModule] = {}

    def resolve(self, value: str, data_dt: datetime | None = None) -> str:
        """Resolve placeholders; dates use ``data_dt`` if given, else ``execution_dt``."""
        with_job = (value or "").replace("${JOB_ID}", self._job_id)
        return resolve_date(resolve_env(with_job), data_dt or self._execution_dt)

    def resolved_path(self, key: str) -> str:
        """Resolve the config value at ``key`` (empty string when the key is absent)."""
        return self.resolve(self._gcs_config.get(key, ""))

    def prefix_for(self, key: str) -> str:
        """Bucket-relative prefix of the resolved path (strips this path's own ``gs://bucket/``)."""
        resolved = self.resolved_path(key)
        if not resolved.startswith("gs://"):
            return resolved
        _, sep, rest = resolved[len("gs://") :].partition("/")
        return rest if sep else resolved

    def module_for(self, key: str) -> GCSModule:
        """Return the :class:`GCSModule` for the bucket named by ``key`` (cached per bucket)."""
        return self.module_for_bucket(self.extract_bucket(self.resolved_path(key)))

    def module_for_bucket(self, bucket: str) -> GCSModule:
        """Return the :class:`GCSModule` for an explicit ``bucket`` name (cached)."""
        if bucket not in self._by_bucket:
            self._by_bucket[bucket] = self._gcs_factory({"project_id": self._project_id, "bucket_name": bucket})
        return self._by_bucket[bucket]

    @staticmethod
    def extract_bucket(gcs_path: str) -> str:
        """Return the bucket name from a ``gs://bucket/key`` URI (empty string if not a URI)."""
        return gcs_path[len("gs://") :].split("/", 1)[0] if gcs_path.startswith("gs://") else ""
