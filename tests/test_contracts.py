"""Application contracts stay provider-neutral, strict, and reproducible."""

from __future__ import annotations

import importlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from evalgen.contracts import (
    APPLICATION_CONTRACT_SCHEMA_VERSION,
    ApplicationContractError,
    ApplicationSpec,
    ContractReference,
    DimensionSpec,
    RETENTION_APPLICATION,
)


def _manifest() -> dict:
    return json.loads(json.dumps(RETENTION_APPLICATION.manifest()))


def test_retention_preset_is_explicit_and_stable():
    assert RETENTION_APPLICATION.manifest() == {
        "schema_version": 1,
        "application_id": "retention",
        "dimensions": [
            {"dimension_id": "call_result", "decision_unit": "call"},
            {"dimension_id": "reason", "decision_unit": "call"},
            {"dimension_id": "product", "decision_unit": "call"},
        ],
        "primary_decision_unit": "call",
        "record_grain": ["call_id", "phone_number", "product"],
        "references": {
            "prompt": {
                "kind": "path",
                "value": "src/evalgen/prompts/manifest.json",
            },
            "schema": {
                "kind": "path",
                "value": "src/evalgen/schemas/retention.json",
            },
            "testset": {
                "kind": "identifier",
                "value": "retention.versioned-testset",
            },
            "adapter": {
                "kind": "identifier",
                "value": "evalharness.adapters.retention:load_csv",
            },
        },
        "default_quality_policy": "decision_grade_v2",
    }
    assert RETENTION_APPLICATION.fingerprint() == (
        "4dce8840ee261bcb491c04a38d8745e8a5f52539b443d028ed30b89d62c3cfc9"
    )


def test_manifest_round_trip_preserves_identity_and_has_canonical_json():
    loaded = ApplicationSpec.from_manifest(_manifest())

    assert loaded == RETENTION_APPLICATION
    assert loaded.fingerprint() == RETENTION_APPLICATION.fingerprint()
    assert loaded.canonical_json() == json.dumps(
        loaded.manifest(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def test_contract_and_its_nested_values_are_immutable():
    with pytest.raises(FrozenInstanceError):
        RETENTION_APPLICATION.application_id = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        RETENTION_APPLICATION.dimensions[0].decision_unit = "changed"  # type: ignore[misc]

    detached = RETENTION_APPLICATION.manifest()
    detached["dimensions"][0]["dimension_id"] = "changed"
    assert RETENTION_APPLICATION.dimensions[0].dimension_id == "call_result"


@pytest.mark.parametrize("field", ["schema_version", "application_id", "references"])
def test_manifest_refuses_missing_top_level_fields(field):
    manifest = _manifest()
    del manifest[field]
    with pytest.raises(ApplicationContractError, match="missing required field"):
        ApplicationSpec.from_manifest(manifest)


def test_manifest_refuses_unknown_fields_at_every_level():
    manifest = _manifest()
    manifest["runtime"] = "openrouter"
    with pytest.raises(ApplicationContractError, match="unknown field"):
        ApplicationSpec.from_manifest(manifest)

    manifest = _manifest()
    manifest["dimensions"][0]["weight"] = 1
    with pytest.raises(ApplicationContractError, match="unknown field"):
        ApplicationSpec.from_manifest(manifest)

    manifest = _manifest()
    manifest["references"]["prompt"]["api_key"] = "must-not-fit"
    with pytest.raises(ApplicationContractError, match="unknown field"):
        ApplicationSpec.from_manifest(manifest)


def test_manifest_refuses_duplicate_or_invalid_dimensions():
    manifest = _manifest()
    manifest["dimensions"].append(dict(manifest["dimensions"][0]))
    with pytest.raises(ApplicationContractError, match="duplicate.*call_result"):
        ApplicationSpec.from_manifest(manifest)

    manifest = _manifest()
    manifest["dimensions"][0]["dimension_id"] = "Call Result"
    with pytest.raises(ApplicationContractError, match="dimension_id"):
        ApplicationSpec.from_manifest(manifest)

    manifest = _manifest()
    manifest["dimensions"] = []
    with pytest.raises(ApplicationContractError, match="non-empty"):
        ApplicationSpec.from_manifest(manifest)


def test_manifest_refuses_an_unrepresented_primary_unit_and_bad_grain():
    manifest = _manifest()
    manifest["primary_decision_unit"] = "conversation"
    with pytest.raises(ApplicationContractError, match="must be used"):
        ApplicationSpec.from_manifest(manifest)

    manifest = _manifest()
    manifest["record_grain"].append("product")
    with pytest.raises(ApplicationContractError, match="record_grain.*unique"):
        ApplicationSpec.from_manifest(manifest)

    manifest = _manifest()
    manifest["record_grain"][0] = "Call ID"
    with pytest.raises(ApplicationContractError, match="lower_snake_case"):
        ApplicationSpec.from_manifest(manifest)


@pytest.mark.parametrize("schema_version", [True, 0, 2, "1"])
def test_manifest_refuses_unknown_or_non_integer_schema_versions(schema_version):
    manifest = _manifest()
    manifest["schema_version"] = schema_version
    with pytest.raises(ApplicationContractError, match="schema_version"):
        ApplicationSpec.from_manifest(manifest)


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("path", "/absolute/schema.json"),
        ("path", "C:/absolute/schema.json"),
        ("path", "../outside.json"),
        ("path", "src\\schema.json"),
        ("identifier", "https://example.test/schema"),
        ("identifier", "sk-secret-material"),
        ("identifier", "module/not-an-identifier"),
    ],
)
def test_references_refuse_machine_specific_or_secret_values(kind, value):
    with pytest.raises(ApplicationContractError):
        ContractReference(kind, value)


def test_direct_construction_requires_immutable_collections():
    values = {
        "application_id": "future_app",
        "dimensions": (DimensionSpec("intent", "call"),),
        "primary_decision_unit": "call",
        "record_grain": ("call_id",),
        "prompt": ContractReference("identifier", "future.prompt:build"),
        "schema": ContractReference("path", "schemas/future.json"),
        "testset": ContractReference("path", "datasets/future_v1.jsonl"),
        "adapter": ContractReference("identifier", "future.adapter:load"),
        "default_quality_policy": "decision_grade_v2",
    }
    contract = ApplicationSpec(**values)
    assert contract.schema_version == APPLICATION_CONTRACT_SCHEMA_VERSION

    with pytest.raises(ApplicationContractError, match="dimensions.*tuple"):
        ApplicationSpec(**{**values, "dimensions": list(values["dimensions"])})
    with pytest.raises(ApplicationContractError, match="record_grain.*tuple"):
        ApplicationSpec(**{**values, "record_grain": ["call_id"]})


def test_retention_references_resolve_inside_this_repository():
    root = Path(__file__).resolve().parents[1]
    for reference in (
        RETENTION_APPLICATION.prompt,
        RETENTION_APPLICATION.schema,
    ):
        assert reference.kind == "path"
        assert (root / reference.value).is_file()

    assert RETENTION_APPLICATION.testset == ContractReference(
        "identifier", "retention.versioned-testset"
    )

    module_name, attribute = RETENTION_APPLICATION.adapter.value.split(":", 1)
    assert callable(getattr(importlib.import_module(module_name), attribute))
