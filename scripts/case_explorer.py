"""Build the per-case explorer: one page showing every test case, its ground truth,
and what each model answered.

WHY THIS EXISTS AS A GENERATOR RATHER THAN A HAND-WRITTEN PAGE

The published comparison reports three weighted-F1 numbers per model. A reader who wants
to know *why* Gemini scores 0.838 on reason has nowhere to look: the number is an
aggregate over 157 rows and eleven one-vs-rest classes, and no single case explains it.
This page is the drill-down -- pick a case, see the transcript that went in, the ground
truth with the evidence that justifies it, and all four models' answers marked right or
wrong.

THE ONE HARD PART, AND WHY IT IS SOLVED THIS WAY

The page lets you filter the case list and recomputes each model's F1 for the slice. That
needs scoring in the browser, and a JavaScript reimplementation of `evalharness.metrics`
would be a SECOND SCORER, free to disagree with the first and be believed anyway.

So the browser never scores. This module precomputes, per model per dimension, the
*scoring atoms* -- the (ground truth, prediction) pairs at that dimension's own grain,
tagged with the call they came from. JavaScript filters those pairs by call and sums them
into one-vs-rest tp/fp/fn. Summation only; every judgement about what joins to what, what
is dropped and what a label means has already happened here, in the real scorer.

The three grains are carried separately and never merged, because that is the thing most
easily broken. `metrics.py` opens by calling three different denominators "the single
most important fact in the file":

    call_result  row grain, rows whose GROUND TRUTH outcome is present   (metrics.py:133)
    reason       row grain, EVERY joined row, no filtering at all        (metrics.py:166)
    product      call grain, groups whose phone key survives             (metrics.py:217)

`verify_atoms` re-aggregates the atoms in Python and refuses to emit unless the result
equals what the real scorer returned, for every model and every dimension. That check is
what makes the browser's arithmetic trustworthy: the atoms are proven to carry the
published number before they are ever shipped.

WHAT IS NOT COMMITTED

The output embeds raw model completions and full transcripts, which CLAUDE.md forbids
committing and `.gitignore` blocks by default. It is written under `out/`, which is
already ignored, so no negation exception is added to a deny-by-default policy. This
script is the committed half; its output is not.

    PYTHONPATH=src python scripts/case_explorer.py
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from evalgen import cli  # noqa: E402
from evalgen.report import _item_is_correct, _signature  # noqa: E402
from evalgen.splits import dilation_blocks  # noqa: E402
from evalharness import labelspaces, metrics  # noqa: E402
from evalharness.records import Record  # noqa: E402

# The four arms of the published comparison, in the order the report uses them.
# Kept in step with docs/reports/model-comparison-metrics.json rather than re-derived: if
# report and this page ever disagree about which run is which model, that is a bug worth
# a loud failure, and `check_shared_contract` below is where it surfaces.
METRICS_JSON = REPO / "docs" / "reports" / "model-comparison-metrics.json"
OUT_HTML = REPO / "out" / "case-explorer.html"
SPLIT_JSON = REPO / "tests" / "fixtures" / "testsets" / "retention_v3.split.json"

SPACE = labelspaces.RETENTION
DIMENSIONS = ("call_result", "reason", "product")


class BuildError(RuntimeError):
    """Raised instead of emitting a page that would look fine and be wrong."""


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------


@dataclass
class Arm:
    key: str
    name: str
    short: str
    ours: bool
    run_id: str
    model_id: str
    meta: dict
    items: list          # TestItem, in testset order
    gt: list[Record]
    replicates: list[list[Record]]   # one Record list per replicate
    results: list                    # ItemResult, for tokens/latency/unscored fields


def load_arm(key: str, label: dict, run_id: str) -> Arm:
    loaded = cli.load_run(REPO / "out" / "runs" / run_id)
    bound = cli._binding_for_run(loaded)
    gt_path = cli._recorded_artifact_path(loaded.directory, loaded.meta["gt_path"])
    ts_path = cli._recorded_artifact_path(loaded.directory, loaded.meta["testset_path"])
    testset = cli._load_testset(ts_path, app="retention")
    gt = cli._load_gt(gt_path, bound=bound)
    reps = cli.replicate_records(loaded.result, testset.items, bound=bound)
    return Arm(
        key=key,
        name=label["name"],
        short=label["short"],
        ours=label["ours"],
        run_id=run_id,
        model_id=loaded.meta.get("model_requested", "?"),
        meta=loaded.meta,
        items=list(testset.items),
        gt=gt,
        replicates=reps,
        results=list(loaded.result.results),
    )


# --------------------------------------------------------------------------------------
# Atoms -- the precomputed scoring inputs the browser is allowed to sum
# --------------------------------------------------------------------------------------


def build_atoms(gt: list[Record], pred: list[Record]) -> dict[str, list]:
    """Reproduce each scorer's row selection, tagging every pair with its call_id.

    Deliberately mirrors `score_call_result` / `score_reason` / `score_product`
    line for line rather than sharing a helper with them: if one of those changes its
    selection, this must fail the verification rather than quietly follow along.
    """
    joined = metrics.outer_join(gt, pred)

    # call_result: metrics.py:142-143 -- GT present, GT call_result not null.
    call_result = [
        [g.call_id, g.call_result, (p.call_result if p is not None else None)]
        for g, p in joined
        if g is not None and g.call_result is not None
    ]

    # reason: metrics.py:172-182 -- every joined row, orphan predictions included.
    reason = []
    for g, p in joined:
        call_id = g.call_id if g is not None else p.call_id
        reason.append(
            [
                call_id,
                (sorted(g.reasons) if g is not None else None),
                (sorted(p.reasons) if p is not None else None),
            ]
        )

    # product: metrics.py:221-230 -- call grain, NaN-phone groups already dropped by
    # _group_products, and keys ordered GT-first exactly as the scorer orders them.
    gt_groups = metrics._group_products(gt)
    pred_groups = metrics._group_products(pred)
    keys = list(gt_groups) + [k for k in pred_groups if k not in gt_groups]
    product = [
        [k[0], sorted(gt_groups.get(k, set())), sorted(pred_groups.get(k, set()))]
        for k in keys
    ]

    return {"call_result": call_result, "reason": reason, "product": product}


def _one_vs_rest(pairs) -> tuple[int, int, int]:
    tp = sum(1 for g, p in pairs if g and p)
    fp = sum(1 for g, p in pairs if not g and p)
    fn = sum(1 for g, p in pairs if g and not p)
    return tp, fp, fn


def f1_from_atoms(atoms: dict[str, list], dimension: str, keep=None) -> float:
    """The exact aggregation the browser performs, written once here so it can be
    checked against the real scorer before it is transcribed into JavaScript.

    `keep` filters by call_id, which is what slicing the case list does.
    """
    rows = atoms[dimension]
    if keep is not None:
        rows = [r for r in rows if r[0] in keep]

    classes = getattr(SPACE, dimension)
    total_w = 0
    acc = 0.0
    for cls in classes:
        if dimension == "call_result":
            pairs = [(g == cls, p == cls) for _, g, p in rows]
        else:
            pairs = [
                (g is not None and cls in g, p is not None and cls in p)
                for _, g, p in rows
            ]
        tp, fp, fn = _one_vs_rest(pairs)
        weight = tp + fn
        if not weight:
            continue
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        acc += f1 * weight
        total_w += weight
    return acc / total_w if total_w else 0.0


def authoritative_f1(gt: list[Record], pred: list[Record]) -> dict[str, float]:
    """What the real scorer says. The number published in the comparison report."""
    return {
        "call_result": metrics.score_call_result(gt, pred, SPACE.call_result).weighted("f1"),
        "reason": metrics.score_reason(gt, pred, SPACE.reason).weighted("f1"),
        "product": metrics.score_product(gt, pred, SPACE.product).weighted("f1"),
    }


def verify_atoms(arm: Arm, atoms: dict[str, list], truth: dict[str, float]) -> list[str]:
    """REFUSAL 1. The atoms must reproduce the published F1 exactly.

    This is the check the live slicing rests on. If aggregating the shipped pairs does
    not return what the scorer returned, then every slice the page computes is arithmetic
    on the wrong inputs -- and it would still render four plausible decimals.
    """
    notes = []
    for dim in DIMENSIONS:
        got = f1_from_atoms(atoms, dim)
        want = truth[dim]
        if not math.isclose(got, want, rel_tol=0, abs_tol=1e-12):
            raise BuildError(
                f"{arm.key}/{dim}: atoms aggregate to {got!r} but the scorer returns "
                f"{want!r}. The page would slice on inputs that do not carry the "
                f"published number."
            )
        notes.append(f"  {arm.key:8} {dim:12} {got:.6f} == scorer")
    return notes


# --------------------------------------------------------------------------------------
# Per-item, per-model detail
# --------------------------------------------------------------------------------------


def by_call(records: list[Record]) -> dict[str, list[Record]]:
    out: dict[str, list[Record]] = {}
    for r in records:
        out.setdefault(r.call_id, []).append(r)
    return out


def rows_for_display(records: list[Record]) -> list[dict]:
    return [
        {
            "product": r.product,
            "call_result": r.call_result,
            "reasons": sorted(r.reasons),
            "parse_ok": r.parse_ok,
        }
        for r in sorted(records, key=lambda r: (r.product or "", r.call_result or ""))
    ]


def dimension_flags(gt_rows: list[Record], pred_rows: list[Record]) -> dict[str, bool]:
    """Per-dimension correctness at each dimension's own grain.

    `report.py` has no per-dimension per-item predicate -- `_item_is_correct` is
    whole-answer, all-or-nothing. These three are derived from the scorers' own grains
    (metrics.py:133-244) and are asserted against `_item_is_correct` in `build`, so the
    page and the report cannot drift apart.
    """
    atoms = build_atoms(gt_rows, pred_rows)
    return {
        "call_result": all(g == p for _, g, p in atoms["call_result"]),
        "reason": all(g == p for _, g, p in atoms["reason"]),
        "product": all(g == p for _, g, p in atoms["product"]),
    }


def payload_extras(payload: dict | None) -> dict:
    """The four fields the model emits that nothing scores.

    Shown, because a reader comparing two answers will otherwise wonder what else was in
    the response; labelled, because showing them unlabelled implies they counted.
    """
    if not isinstance(payload, dict):
        return {"recommendation": None, "call_event_detection": None, "keywords": {}}
    keywords: dict[str, dict[str, str]] = {}
    blocks = payload.get("product")
    if isinstance(blocks, dict):
        for name, block in blocks.items():
            if not isinstance(block, dict):
                continue
            slots = {}
            for slot in ("main", "secondary", "third"):
                cell = block.get(slot)
                if isinstance(cell, dict) and cell.get("keyword"):
                    slots[slot] = cell["keyword"]
            if slots:
                keywords[name] = slots
    return {
        "recommendation": payload.get("recommendation"),
        "call_event_detection": payload.get("call_event_detection"),
        "keywords": keywords,
    }


def parse_turns(transcript: str) -> list[dict]:
    turns = []
    for line in transcript.split("\n"):
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            who, _, text = line.partition(":")
            turns.append({"who": who.strip(), "text": text.strip()})
        else:
            turns.append({"who": "", "text": line})
    return turns


def parse_annotations(mapping: dict) -> list[dict]:
    """`ev_reason:dissatisfied service#2` -> {dim, label, ordinal, value}."""
    out = []
    for raw_key, value in (mapping or {}).items():
        key = raw_key.split("_", 1)[1] if "_" in raw_key else raw_key
        dim, _, label = key.partition(":")
        ordinal = 1
        if "#" in label:
            label, _, num = label.partition("#")
            ordinal = int(num) if num.isdigit() else 1
        out.append(
            {"dim": dim, "label": label.strip(), "ordinal": ordinal, "value": value}
        )
    return sorted(out, key=lambda a: (a["dim"], a["label"], a["ordinal"]))


# --------------------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------------------


CONTRACT_KEYS = ("testset_sha", "prompt_sha", "scoring_code_sha", "repeats", "items")


def check_shared_contract(arms: list[Arm]) -> dict:
    """REFUSAL 2. Four runs are only comparable if they share one contract.

    The page puts four models' answers to "the same question" side by side. That claim is
    checked here rather than remembered: a differing testset or prompt sha means the
    question was not the same, and the columns are not answers to it.
    """
    shared = {}
    for key in CONTRACT_KEYS:
        values = {a.key: a.meta.get(key) for a in arms}
        distinct = set(values.values())
        if len(distinct) != 1:
            raise BuildError(
                f"runs disagree on {key}: {values}. They cannot be shown as four "
                f"answers to the same question."
            )
        shared[key] = distinct.pop()
    return shared


def build() -> dict:
    metrics_doc = json.loads(METRICS_JSON.read_text(encoding="utf-8"))
    order = metrics_doc["order"]
    labels = metrics_doc["labels"]

    arms = [
        load_arm(key, labels[key], metrics_doc["models"][key]["run_id"]) for key in order
    ]
    shared = check_shared_contract(arms)

    ref = arms[0]
    items = ref.items
    gt = ref.gt

    # REFUSAL 3. Shape. A silently short testset or a half-written run would otherwise
    # produce a page that reads as complete.
    if len(items) != shared["items"]:
        raise BuildError(f"{len(items)} items loaded, contract says {shared['items']}")
    if len(gt) != 150:
        raise BuildError(f"{len(gt)} ground-truth rows, expected 150 for retention_v3")
    for arm in arms:
        if len(arm.results) != shared["items"] * shared["repeats"]:
            raise BuildError(
                f"{arm.key}: {len(arm.results)} rows, expected "
                f"{shared['items'] * shared['repeats']}"
            )
        if len(arm.replicates) != shared["repeats"]:
            raise BuildError(f"{arm.key}: {len(arm.replicates)} replicates")

    split = json.loads(SPLIT_JSON.read_text(encoding="utf-8"))
    tune = set(split["tune"])
    # `dilation_blocks` is keyed by MEMBER, not by base -- each member maps to the whole
    # block tuple, whose first element is the base (splits.py:88). Reading the key as the
    # base makes every member of a block overwrite the last, which is how RET-16 came to
    # describe itself as a dilation of its own dilation.
    blocks = dilation_blocks(items)
    dilation = {}
    for member, members in blocks.items():
        if len(members) < 2:
            continue
        base = members[0]
        dilation[member] = {
            "base": base,
            "is_base": member == base,
            "members": list(members),
        }

    gt_by_call = by_call(gt)
    notes: list[str] = []
    truth: dict[str, dict[str, float]] = {}
    atoms: dict[str, dict[str, list]] = {}

    for arm in arms:
        scored_rep = arm.replicates[0]
        truth[arm.key] = authoritative_f1(gt, scored_rep)
        atoms[arm.key] = build_atoms(gt, scored_rep)
        notes.extend(verify_atoms(arm, atoms[arm.key], truth[arm.key]))

    out_items = []
    always_correct = {a.key: 0 for a in arms}
    flag_mismatches = []

    for item in items:
        gt_rows = gt_by_call.get(item.call_id, [])
        entry = {
            "item_id": item.item_id,
            "call_id": item.call_id,
            "phone": item.phone_number,
            "family": item.family,
            "slice": "tune" if item.item_id in tune else "holdout",
            "turns": parse_turns(item.transcript_th),
            "chars": len(item.transcript_th),
            "mechanism": item.mechanism,
            "why_it_matters": item.why_it_matters,
            "expected_failure": item.expected_failure,
            "evidence": parse_annotations(item.evidence),
            "rules": parse_annotations(item.rules),
            "dilation": dilation.get(item.item_id),
            "gt": rows_for_display(gt_rows),
            "models": {},
        }

        for arm in arms:
            per_rep = [by_call(rep).get(item.call_id, []) for rep in arm.replicates]
            scored_rows = per_rep[0]
            flags = dimension_flags(gt_rows, scored_rows)
            correct = _item_is_correct(gt_rows, scored_rows)

            # The consistency check that keeps this page honest against report.py:
            # _item_is_correct compares exactly phone, product, call_result and reasons,
            # so it must equal the conjunction of the three per-dimension flags.
            if correct != all(flags.values()):
                flag_mismatches.append((item.item_id, arm.key, correct, dict(flags)))

            signatures = [_signature(rows) for rows in per_rep]
            stable = len(set(signatures)) == 1
            hits = sum(_item_is_correct(gt_rows, rows) for rows in per_rep)
            if hits == len(per_rep):
                always_correct[arm.key] += 1

            res = next(
                (r for r in arm.results if r.item_id == item.item_id and r.replicate == 1),
                None,
            )

            entry["models"][arm.key] = {
                "rows": rows_for_display(scored_rows),
                "correct": correct,
                "dims": flags,
                "stable": stable,
                "hits": hits,
                "reps": None if stable else [rows_for_display(r) for r in per_rep],
                "extras": payload_extras(res.payload if res else None),
                "tokens": {
                    "in": (res.prompt_tokens if res else None),
                    "out": (res.completion_tokens if res else None),
                },
                "latency": (round(res.latency_s, 3) if res and res.latency_s else None),
                "parse_ok": (res.parse_ok if res else None),
            }

        out_items.append(entry)

    # REFUSAL 4. The two correctness definitions must agree on every case.
    if flag_mismatches:
        raise BuildError(
            "per-dimension flags disagree with report.py's _item_is_correct on "
            f"{len(flag_mismatches)} case(s): {flag_mismatches[:5]}. One of the two "
            "definitions is wrong and the page would show both."
        )

    return {
        "generated_from": {
            "runs": {a.key: a.run_id for a in arms},
            "testset": "retention_v3",
            "scored_replicate": 1,
        },
        "shared_contract": shared,
        "split_contamination": split["contamination"],
        "order": order,
        "models": [
            {
                "key": a.key,
                "name": a.name,
                "short": a.short,
                "ours": a.ours,
                "run_id": a.run_id,
                "model_id": a.model_id,
                "always_correct": always_correct[a.key],
            }
            for a in arms
        ],
        "classes": {d: list(getattr(SPACE, d)) for d in DIMENSIONS},
        "authoritative_f1": truth,
        "items": out_items,
        "atoms": atoms,
        "verification": notes,
    }


if __name__ == "__main__":
    import case_explorer_html

    data = build()
    print("atoms reproduce the scorer:")
    print("\n".join(data["verification"]))
    print()
    print("items          ", len(data["items"]))
    print("always correct ", {m["key"]: m["always_correct"] for m in data["models"]})

    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(case_explorer_html.render(data), encoding="utf-8")
    print("wrote          ", OUT_HTML, f"{OUT_HTML.stat().st_size / 1e6:.2f} MB")
