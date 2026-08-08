"""Provider-neutral application contracts for reproducible evaluations.

Runtime configuration answers *where* a model runs.  This module answers the
orthogonal question: *what application contract is being evaluated?*  Keeping those
identities separate lets the same application be evaluated through OpenRouter, an
internal OpenAI-compatible GPU endpoint, or a future runtime without changing its
scoring meaning.

The objects are immutable and contain references, never artifact contents or secrets.
Paths are repository-relative POSIX paths; identifiers name code-level integration
points.  A canonical JSON representation is hashed so every run can record the exact
application contract it used.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal

__all__ = [
    "APPLICATION_CONTRACT_SCHEMA_VERSION",
    "ApplicationContractError",
    "ApplicationSpec",
    "ContractReference",
    "DimensionSpec",
    "RETENTION_APPLICATION",
]

APPLICATION_CONTRACT_SCHEMA_VERSION = 1

ReferenceKind = Literal["identifier", "path"]

_NAME = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_CODE_IDENTIFIER = re.compile(
    r"^[A-Za-z][A-Za-z0-9_.-]*(?::[A-Za-z][A-Za-z0-9_.-]*)?$"
)
_OBVIOUS_SECRET_PREFIXES = (
    "api_key=",
    "apikey=",
    "bearer ",
    "password=",
    "secret=",
    "sk-",
    "token=",
)


class ApplicationContractError(ValueError):
    """A manifest is incomplete, ambiguous, unsafe, or from an unknown version."""


@dataclass(frozen=True, slots=True)
class ContractReference:
    """A non-secret, machine-independent reference to one contract component."""

    kind: ReferenceKind
    value: str

    def __post_init__(self) -> None:
        if self.kind not in {"identifier", "path"}:
            raise ApplicationContractError(
                "reference kind must be 'identifier' or 'path'"
            )
        if not isinstance(self.value, str) or not self.value:
            raise ApplicationContractError("reference value must be a non-empty string")
        if self.value != self.value.strip():
            raise ApplicationContractError(
                "reference value must not have leading or trailing whitespace"
            )
        if len(self.value) > 512 or any(ord(char) < 32 for char in self.value):
            raise ApplicationContractError(
                "reference value is too long or contains a control character"
            )
        lowered = self.value.casefold()
        if lowered.startswith(_OBVIOUS_SECRET_PREFIXES):
            raise ApplicationContractError(
                "contract references must name artifacts, never contain credentials"
            )
        if "://" in self.value:
            raise ApplicationContractError(
                "contract references must be repository paths or identifiers, not URLs"
            )

        if self.kind == "identifier":
            if not _CODE_IDENTIFIER.fullmatch(self.value):
                raise ApplicationContractError(
                    f"invalid code identifier reference {self.value!r}"
                )
            return

        if "\\" in self.value:
            raise ApplicationContractError(
                "path references must use repository-relative POSIX separators"
            )
        path = PurePosixPath(self.value)
        if (
            path.is_absolute()
            or PureWindowsPath(self.value).is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.as_posix() != self.value
        ):
            raise ApplicationContractError(
                f"path reference must be a normalized repository-relative path: "
                f"{self.value!r}"
            )

    def manifest(self) -> dict[str, str]:
        """Return the JSON-compatible representation, with no live object aliases."""
        return {"kind": self.kind, "value": self.value}

    @classmethod
    def from_manifest(cls, value: object, *, field: str) -> ContractReference:
        mapping = _mapping(value, field)
        _require_exact_fields(mapping, {"kind", "value"}, field)
        kind = mapping["kind"]
        reference = mapping["value"]
        if not isinstance(kind, str) or not isinstance(reference, str):
            raise ApplicationContractError(
                f"{field}.kind and {field}.value must be strings"
            )
        return cls(kind=kind, value=reference)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class DimensionSpec:
    """One scored dimension and the unit on which its paired decision is made."""

    dimension_id: str
    decision_unit: str

    def __post_init__(self) -> None:
        _validate_name(self.dimension_id, "dimension_id")
        _validate_name(self.decision_unit, "decision_unit")

    def manifest(self) -> dict[str, str]:
        return {
            "dimension_id": self.dimension_id,
            "decision_unit": self.decision_unit,
        }

    @classmethod
    def from_manifest(cls, value: object, *, index: int) -> DimensionSpec:
        field = f"dimensions[{index}]"
        mapping = _mapping(value, field)
        _require_exact_fields(mapping, {"dimension_id", "decision_unit"}, field)
        dimension_id = mapping["dimension_id"]
        decision_unit = mapping["decision_unit"]
        if not isinstance(dimension_id, str) or not isinstance(decision_unit, str):
            raise ApplicationContractError(
                f"{field}.dimension_id and {field}.decision_unit must be strings"
            )
        return cls(dimension_id=dimension_id, decision_unit=decision_unit)


@dataclass(frozen=True, slots=True)
class ApplicationSpec:
    """Versioned definition of one application's evaluation semantics.

    ``record_grain`` describes the normalized scorer row.  ``primary_decision_unit``
    describes the unit at which migration evidence is clustered, and must be used by
    at least one dimension.  Per-dimension units make deliberate exceptions explicit
    (for example, product-set correctness is naturally decided once per call).
    """

    application_id: str
    dimensions: tuple[DimensionSpec, ...]
    primary_decision_unit: str
    record_grain: tuple[str, ...]
    prompt: ContractReference
    schema: ContractReference
    testset: ContractReference
    adapter: ContractReference
    default_quality_policy: str
    schema_version: int = APPLICATION_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != APPLICATION_CONTRACT_SCHEMA_VERSION
        ):
            raise ApplicationContractError(
                "schema_version must be "
                f"{APPLICATION_CONTRACT_SCHEMA_VERSION}"
            )
        _validate_name(self.application_id, "application_id")
        _validate_name(self.primary_decision_unit, "primary_decision_unit")
        _validate_name(self.default_quality_policy, "default_quality_policy")

        if not isinstance(self.dimensions, tuple) or not self.dimensions:
            raise ApplicationContractError(
                "dimensions must be a non-empty tuple of DimensionSpec values"
            )
        if not all(isinstance(item, DimensionSpec) for item in self.dimensions):
            raise ApplicationContractError(
                "dimensions must contain only DimensionSpec values"
            )
        dimension_ids = [item.dimension_id for item in self.dimensions]
        duplicate_dimensions = _duplicates(dimension_ids)
        if duplicate_dimensions:
            raise ApplicationContractError(
                "dimension_id values must be unique; duplicate(s): "
                + ", ".join(duplicate_dimensions)
            )
        if self.primary_decision_unit not in {
            item.decision_unit for item in self.dimensions
        }:
            raise ApplicationContractError(
                "primary_decision_unit must be used by at least one dimension"
            )

        if not isinstance(self.record_grain, tuple) or not self.record_grain:
            raise ApplicationContractError(
                "record_grain must be a non-empty tuple of field names"
            )
        if not all(isinstance(field, str) for field in self.record_grain):
            raise ApplicationContractError(
                "record_grain must contain only string field names"
            )
        for field in self.record_grain:
            if not _FIELD_NAME.fullmatch(field):
                raise ApplicationContractError(
                    f"invalid record_grain field {field!r}; use lower_snake_case"
                )
        duplicate_fields = _duplicates(self.record_grain)
        if duplicate_fields:
            raise ApplicationContractError(
                "record_grain fields must be unique; duplicate(s): "
                + ", ".join(duplicate_fields)
            )

        for field in ("prompt", "schema", "testset", "adapter"):
            if not isinstance(getattr(self, field), ContractReference):
                raise ApplicationContractError(
                    f"{field} must be a ContractReference"
                )

    def manifest(self) -> dict[str, Any]:
        """Return the complete, canonical-shape, JSON-compatible contract."""
        return {
            "schema_version": self.schema_version,
            "application_id": self.application_id,
            "dimensions": [item.manifest() for item in self.dimensions],
            "primary_decision_unit": self.primary_decision_unit,
            "record_grain": list(self.record_grain),
            "references": {
                "prompt": self.prompt.manifest(),
                "schema": self.schema.manifest(),
                "testset": self.testset.manifest(),
                "adapter": self.adapter.manifest(),
            },
            "default_quality_policy": self.default_quality_policy,
        }

    def canonical_json(self) -> str:
        """The byte-for-byte JSON text used by :meth:`fingerprint`."""
        return json.dumps(
            self.manifest(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def fingerprint(self) -> str:
        """SHA-256 identity of the full application contract."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_manifest(cls, value: object) -> ApplicationSpec:
        """Parse a strict manifest; no unknown or implicit fields are accepted."""
        mapping = _mapping(value, "application contract")
        _require_exact_fields(
            mapping,
            {
                "schema_version",
                "application_id",
                "dimensions",
                "primary_decision_unit",
                "record_grain",
                "references",
                "default_quality_policy",
            },
            "application contract",
        )

        schema_version = mapping["schema_version"]
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            raise ApplicationContractError("schema_version must be an integer")
        application_id = mapping["application_id"]
        primary_decision_unit = mapping["primary_decision_unit"]
        default_quality_policy = mapping["default_quality_policy"]
        if not all(
            isinstance(item, str)
            for item in (
                application_id,
                primary_decision_unit,
                default_quality_policy,
            )
        ):
            raise ApplicationContractError(
                "application_id, primary_decision_unit, and "
                "default_quality_policy must be strings"
            )

        raw_dimensions = mapping["dimensions"]
        if not isinstance(raw_dimensions, list):
            raise ApplicationContractError("dimensions must be a JSON array")
        dimensions = tuple(
            DimensionSpec.from_manifest(item, index=index)
            for index, item in enumerate(raw_dimensions)
        )

        raw_grain = mapping["record_grain"]
        if not isinstance(raw_grain, list):
            raise ApplicationContractError("record_grain must be a JSON array")
        if not all(isinstance(item, str) for item in raw_grain):
            raise ApplicationContractError(
                "record_grain must contain only string field names"
            )

        references = _mapping(mapping["references"], "references")
        _require_exact_fields(
            references,
            {"prompt", "schema", "testset", "adapter"},
            "references",
        )
        return cls(
            schema_version=schema_version,
            application_id=application_id,
            dimensions=dimensions,
            primary_decision_unit=primary_decision_unit,
            record_grain=tuple(raw_grain),
            prompt=ContractReference.from_manifest(
                references["prompt"], field="references.prompt"
            ),
            schema=ContractReference.from_manifest(
                references["schema"], field="references.schema"
            ),
            testset=ContractReference.from_manifest(
                references["testset"], field="references.testset"
            ),
            adapter=ContractReference.from_manifest(
                references["adapter"], field="references.adapter"
            ),
            default_quality_policy=default_quality_policy,
        )


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ApplicationContractError(f"{field} must be a JSON object")
    if not all(isinstance(key, str) for key in value):
        raise ApplicationContractError(f"{field} keys must be strings")
    return value


def _require_exact_fields(
    value: Mapping[str, object], expected: set[str], field: str
) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise ApplicationContractError(
            f"{field} is missing required field(s): {', '.join(missing)}"
        )
    if unknown:
        raise ApplicationContractError(
            f"{field} has unknown field(s): {', '.join(unknown)}"
        )


def _validate_name(value: object, field: str) -> None:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ApplicationContractError(
            f"{field} must be a lowercase portable identifier"
        )


def _duplicates(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


# Compatibility preset for the application's current production-fidelity scorer.  The
# testset reference names the versioned-testset contract rather than one concrete pack;
# each run separately snapshots and hashes the exact testset and ground truth it used.
RETENTION_APPLICATION = ApplicationSpec(
    application_id="retention",
    dimensions=(
        DimensionSpec("call_result", "call"),
        DimensionSpec("reason", "call"),
        DimensionSpec("product", "call"),
    ),
    primary_decision_unit="call",
    record_grain=("call_id", "phone_number", "product"),
    prompt=ContractReference("path", "src/evalgen/prompts/manifest.json"),
    schema=ContractReference("path", "src/evalgen/schemas/retention.json"),
    testset=ContractReference("identifier", "retention.versioned-testset"),
    adapter=ContractReference(
        "identifier", "evalharness.adapters.retention:load_csv"
    ),
    default_quality_policy="decision_grade_v2",
)
