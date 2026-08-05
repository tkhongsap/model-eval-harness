"""Tests for GcsRouter — per-bucket routing and placeholder resolution.

Each ``gcs.*`` path may name a different bucket within the same project; the router must
route every operation to the bucket of *its own* path and strip foreign buckets correctly.
``${JOB_ID}`` must be substituted before env resolution; ``%{DATA_DATE...}`` resolves with
``execution_dt`` unless a ``data_dt`` override is supplied.
"""

from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from tasks.ocr_tax_invoice_pipeline.module.gcs_router import GcsRouter

EXECUTION_DT = datetime(2026, 6, 5, 13, 35, 13, tzinfo=ZoneInfo("UTC"))

GCS_CONFIG = {
    "project_id": "gcs-proj",
    # bucket-a
    "landing_path": "gs://bucket-a/ocr_landing/${JOB_ID}",
    "processing_path": "gs://bucket-a/ocr_processing/docs/${JOB_ID}",
    # bucket-b (foreign)
    "payload_landing_path": "gs://bucket-b/ocr_processing/payloads/${JOB_ID}",
    "output_path": "gs://bucket-b/ocr_processing/output/${JOB_ID}",
    # dated
    "dated_path": "gs://bucket-a/result/%{DATA_DATE_YYYYMM}/out.csv",
}


def _fake_factory(cfg):
    """Return a stub GCSModule echoing the requested bucket/project."""
    return Mock(bucket_name=cfg["bucket_name"], project_id=cfg["project_id"])


def _router(factory=_fake_factory):
    return GcsRouter(GCS_CONFIG, job_id="JOB", execution_dt=EXECUTION_DT, gcs_factory=factory)


class TestModuleRouting:
    def test_routes_each_path_to_its_own_bucket(self):
        router = _router()
        assert router.module_for("landing_path").bucket_name == "bucket-a"
        assert router.module_for("processing_path").bucket_name == "bucket-a"
        assert router.module_for("payload_landing_path").bucket_name == "bucket-b"
        assert router.module_for("output_path").bucket_name == "bucket-b"

    def test_same_bucket_keys_share_one_cached_module(self):
        factory = Mock(side_effect=_fake_factory)
        router = _router(factory)

        landing = router.module_for("landing_path")
        processing = router.module_for("processing_path")  # same bucket-a
        payload = router.module_for("payload_landing_path")  # bucket-b

        assert landing is processing  # cached, not rebuilt
        assert payload is not landing
        assert factory.call_count == 2  # one module per distinct bucket

    def test_module_for_bucket_caches_and_uses_router_project(self):
        factory = Mock(side_effect=_fake_factory)
        router = _router(factory)

        first = router.module_for_bucket("bucket-z")
        second = router.module_for_bucket("bucket-z")

        assert first is second
        assert factory.call_count == 1
        assert factory.call_args[0][0] == {"project_id": "gcs-proj", "bucket_name": "bucket-z"}


class TestPrefixFor:
    def test_strips_each_paths_own_bucket(self):
        router = _router()
        assert router.prefix_for("payload_landing_path") == "ocr_processing/payloads/JOB"
        assert router.prefix_for("landing_path") == "ocr_landing/JOB"

    def test_prefix_has_no_scheme(self):
        router = _router()
        assert not router.prefix_for("output_path").startswith("gs://")


class TestResolve:
    def test_job_id_substituted_before_env(self):
        # ${JOB_ID} must be replaced before resolve_env, which would otherwise blank it.
        router = _router()
        assert router.resolved_path("landing_path") == "gs://bucket-a/ocr_landing/JOB"

    def test_data_date_resolves_with_execution_dt(self):
        router = _router()
        assert router.resolved_path("dated_path") == "gs://bucket-a/result/202606/out.csv"

    def test_resolve_honours_data_dt_override(self):
        router = _router()
        out = router.resolve(GCS_CONFIG["dated_path"], data_dt=datetime(2026, 1, 9))
        assert out == "gs://bucket-a/result/202601/out.csv"

    def test_missing_key_resolves_to_empty_string(self):
        router = _router()
        assert router.resolved_path("nonexistent") == ""

    def test_extract_bucket_static_helper(self):
        assert GcsRouter.extract_bucket("gs://bucket-a/x/y") == "bucket-a"
        assert GcsRouter.extract_bucket("not-a-uri") == ""
