"""What `--app` actually selects: one binding per executable application.

The gap this closes
-------------------
`contracts.ApplicationSpec` has modelled an application as data since it was written --
`dimensions`, `record_grain`, `primary_decision_unit`, and references to a prompt, schema,
testset and adapter. It is hashed into every run and verified on load. **Nothing read
it.** `cli.APPLICATIONS` was a one-entry dict used only to look the spec up and hash it,
while `cli._SCORERS` was a module-level constant naming `RETENTION`'s three label tuples
directly.

The consequence was concrete rather than theoretical. `--app` existed, `testsets` already
carried a per-app `LabelSpace` registry, and `labelspaces.MNP` was already declared -- so
`--app mnp` would *validate* a pack against MNP's twelve reason classes and then *score*
it against Retention's eleven. Not a crash: a number, slightly wrong, in the same shape as
a right one. That is the failure this repository exists to prevent, and it was reachable
from the command line.

An `AppBinding` is the executable half of a spec: the spec says what the application
means, the binding says which code implements it. They are built together and checked
against each other, so an application cannot be half-added.

Why this module is in `evalgen` and not `evalharness`
-----------------------------------------------------
It has to reference `flatten`, the prompt assets and the schema files, all of which live
here, and `tests/test_boundary.py::test_evalharness_never_imports_evalgen` forbids the
import that would let it live on the other side. The scoring functions it binds are
imported *from* `evalharness`, which is the direction the boundary allows.

What the builder refuses, and why each refusal is not paranoia
--------------------------------------------------------------
* **A declared dimension with no scorer.** Adding `DimensionSpec("issue_type", ...)` to a
  spec without wiring an implementation would otherwise drop that dimension silently: the
  contract would claim four scored dimensions, the report would print three, and both
  would look complete.
* **A scorer for an undeclared dimension.** The reverse, and worse -- a number appears in
  the report that the hashed contract does not mention.
* **An empty class tuple.** This is the specific guard that stops the MNP defect above.
  One-vs-rest over `()` yields no classes, so every weighted average is `0/0`, and a
  dimension that scored nothing reads as a dimension that scored zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from evalharness.adapters.registry import resolve_adapter
from evalharness.labelspaces import RETENTION, LabelSpace
from evalharness.metrics import (
    DimensionResult,
    score_call_result,
    score_product,
    score_reason,
)
from evalharness.records import Record

from . import flatten
from .contracts import RETENTION_APPLICATION, ApplicationSpec
from .decoding import decoding_schema

__all__ = [
    "AppBinding",
    "AppBindingError",
    "BINDINGS",
    "binding",
    "build_binding",
]

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent

Scorer = Callable[..., DimensionResult]


class AppBindingError(ValueError):
    """Raised when a spec and its implementation do not agree."""


@dataclass(frozen=True)
class AppBinding:
    """One application's contract plus the code that implements it."""

    spec: ApplicationSpec
    label_space: LabelSpace
    scorers: tuple[tuple[str, Scorer, tuple[str, ...]], ...]
    schema_path: Path
    decoding_transform: Callable[[Mapping[str, Any]], dict[str, Any]]
    flatten_rows: Callable[..., list[dict[str, Any]]]
    load_gt: Callable[..., list[Record]]
    default_testset: Path
    default_gt: Path
    cost_per_correct_dimension: str
    report_title: str
    # Diagnostics coupled to this application's PAYLOAD shape rather than its record
    # shape, so that an app absent from a diagnostic's set can be refused by the command
    # rather than silently mis-scored: `fabrication` run against the wrong payload shape
    # returns "0 fabricated labels", which is the shape of a clean bill of health.
    #
    # DECLARED AND NOT YET CONSUMED. `cmd_judge` and `cmd_severity` do not read this, so
    # the refusal it is for does not exist yet. It is stated now because the set is part
    # of what makes an application reviewable, and it lands with the second application --
    # which is when there is a second value to refuse. Recorded plainly rather than
    # written as though finished: retention is the only binding, so nothing here can be
    # exercised, and a comment describing a working refusal would be the only evidence of
    # one.
    diagnostics: frozenset[str]

    @property
    def app(self) -> str:
        return self.spec.application_id

    @property
    def dimension_ids(self) -> tuple[str, ...]:
        """Scored dimensions, in the contract's own order.

        The spec's order is the report's order. Sorting here would silently re-rank the
        report against the document that defines the application.
        """
        return tuple(item.dimension_id for item in self.spec.dimensions)


def _build_scorers(
    spec: ApplicationSpec,
    label_space: LabelSpace,
    implementations: Mapping[str, Scorer],
) -> tuple[tuple[str, Scorer, tuple[str, ...]], ...]:
    """Derive the scorer table from the contract. Never retype it.

    Class tuples are read off the `LabelSpace` by dimension name, so an application whose
    vocabulary differs is a data change and not a code fork -- which is what
    `labelspaces.py` promised and nothing yet delivered.
    """
    declared = [item.dimension_id for item in spec.dimensions]

    missing = [name for name in declared if name not in implementations]
    if missing:
        raise AppBindingError(
            f"{spec.application_id}: contract declares dimension(s) {missing} with no "
            "scorer. A declared dimension with no implementation is dropped silently: "
            "the contract would claim it and the report would omit it."
        )

    undeclared = [name for name in implementations if name not in declared]
    if undeclared:
        raise AppBindingError(
            f"{spec.application_id}: scorer(s) {undeclared} are not declared in the "
            "application contract. A dimension the hashed contract does not mention "
            "must not appear in the report."
        )

    table: list[tuple[str, Scorer, tuple[str, ...]]] = []
    for name in declared:
        classes = getattr(label_space, name, None)
        if classes is None:
            raise AppBindingError(
                f"{spec.application_id}: label space has no class list for dimension "
                f"{name!r}"
            )
        if not classes:
            raise AppBindingError(
                f"{spec.application_id}: dimension {name!r} has an EMPTY class list. "
                "One-vs-rest over no classes makes every weighted average 0/0, and a "
                "dimension that scored nothing prints exactly like one that scored zero. "
                "Either give it a vocabulary or do not declare it."
            )
        table.append((name, implementations[name], tuple(classes)))
    return tuple(table)


def build_binding(
    spec: ApplicationSpec,
    *,
    label_space: LabelSpace,
    implementations: Mapping[str, Scorer],
    flatten_rows: Callable[..., list[dict[str, Any]]],
    decoding_transform: Callable[[Mapping[str, Any]], dict[str, Any]],
    default_testset: Path,
    default_gt: Path,
    cost_per_correct_dimension: str,
    report_title: str,
    diagnostics: frozenset[str],
    root: Path | None = None,
) -> AppBinding:
    """Bind one reviewed contract to the code implementing it, or refuse."""
    repo = (root or REPO_ROOT).resolve()

    if label_space.app != spec.application_id:
        raise AppBindingError(
            f"label space is for {label_space.app!r} but the contract is for "
            f"{spec.application_id!r}"
        )

    if spec.schema.kind != "path":
        raise AppBindingError(
            f"{spec.application_id}: schema reference must be a path, found "
            f"{spec.schema.kind!r}"
        )
    schema_path = (repo / spec.schema.value).resolve()
    if not schema_path.is_file():
        raise AppBindingError(
            f"{spec.application_id}: schema {spec.schema.value!r} does not exist at "
            f"{schema_path}"
        )

    if spec.adapter.kind != "identifier":
        raise AppBindingError(
            f"{spec.application_id}: adapter reference must be an identifier, found "
            f"{spec.adapter.kind!r}"
        )
    load_gt = resolve_adapter(spec.adapter.value)

    scorers = _build_scorers(spec, label_space, implementations)

    if cost_per_correct_dimension not in {name for name, _, _ in scorers}:
        raise AppBindingError(
            f"{spec.application_id}: cost-per-correct dimension "
            f"{cost_per_correct_dimension!r} is not one of this application's scored "
            "dimensions, so the ratio would divide by a number the report never prints."
        )

    return AppBinding(
        spec=spec,
        label_space=label_space,
        scorers=scorers,
        schema_path=schema_path,
        decoding_transform=decoding_transform,
        flatten_rows=flatten_rows,
        load_gt=load_gt,
        default_testset=default_testset,
        default_gt=default_gt,
        cost_per_correct_dimension=cost_per_correct_dimension,
        report_title=report_title,
        diagnostics=diagnostics,
    )


_TESTSETS = REPO_ROOT / "tests" / "fixtures" / "testsets"

RETENTION_BINDING = build_binding(
    RETENTION_APPLICATION,
    label_space=RETENTION,
    implementations={
        "call_result": score_call_result,
        "reason": score_reason,
        "product": score_product,
    },
    flatten_rows=flatten.to_rows,
    decoding_transform=decoding_schema,
    default_testset=_TESTSETS / "retention_v1.jsonl",
    default_gt=_TESTSETS / "retention_v1.gt.csv",
    cost_per_correct_dimension="call_result",
    report_title="RETENTION EVAL",
    diagnostics=frozenset(
        {"judge", "severity", "evidence", "fabrication", "stability_attribution"}
    ),
)

BINDINGS: dict[str, AppBinding] = {RETENTION_BINDING.app: RETENTION_BINDING}


def binding(application_id: str) -> AppBinding:
    """The executable binding for one application, or a refusal naming what is missing.

    Kept deliberately parallel to `cli._application_spec`'s message: a label space alone
    does not make an application runnable, and this is the point at which that becomes
    true rather than aspirational.
    """
    try:
        return BINDINGS[application_id]
    except KeyError as exc:
        raise AppBindingError(
            f"application {application_id!r} has no executable binding. Known: "
            f"{sorted(BINDINGS)}. Define and review its adapter, prompt, schema, testset "
            "reference and decision units before running models."
        ) from exc
