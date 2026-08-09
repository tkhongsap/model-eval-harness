"""An independent model's opinion on scorer disagreements -- diagnostic, never a verdict.

What this measures
-------------------
Every experiment in this repository trusts one thing without an automated check: that
`retention_v*.gt.csv` is right. RET-11's ground-truth defect (`EXPERIMENTS.md`,
Experiment 1) was found by a human reading the transcript against the production rule by
hand. This module automates that same question -- **is the disputed label actually
correct, or is the "wrong" answer defensible, or is the ground truth itself the
mistake?** -- for every item where the harness's own scorer says an arm disagrees with
ground truth, using a model that is not one of the arms being evaluated.

It answers a different question than `evidence.py` and `fabrication.py`. Those ask
"is this text really in the transcript" -- purely mechanical, no judgment involved. This
module asks "is this label defensible", which is a judgment call, made by a third model,
recorded as one opinion and never elevated past that.

Nothing here is scored, ranked, or joins a verdict
---------------------------------------------------
Same constraint `evidence.py` states and the same reason: a diagnostic that quietly
became a fourth dimension would let a judge's opinion overrule the pre-registered
ground truth by the back door. Enforced three ways, not just claimed in prose:

  * `test_evalharness_never_imports_evalgen` (`tests/test_boundary.py`) already makes it
    impossible for `src/evalharness/` -- where every scored dimension is computed -- to
    import this module at all, transitively, because this module lives in `evalgen`.
  * `tests/test_judge.py::test_judge_is_never_imported_by_the_verdict_path` parses the
    AST of `report.py` and checks it directly, rather than trusting this paragraph, which
    is a stronger guarantee than `evidence.py` carries today (that module's isolation is
    asserted in its own docstring and has never been checked by a test).
  * `run_judge` returns a `summary` dict with no `net`, no `verdict`, and no field named
    anything `compare.PairedVerdict` or `report.MechanismRow` would recognise. The
    closest thing to a headline number here is `ground_truth_error_rate`, and it is named
    for exactly what it is: how often a third model thinks the pack's own ground truth is
    wrong, not who won.

The verdict space, and why it is four-way rather than a yes/no
----------------------------------------------------------------
| Verdict | Means |
|---|---|
| `ground_truth_correct` | The disputed answer is simply wrong; ground truth holds. |
| `defensible_disagreement` | The disputed answer is ALSO reasonable given the transcript -- a genuine ambiguity, not a fabrication. |
| `ground_truth_error` | The judge believes the ground truth itself is the mistake. The strong claim: this is what would have caught RET-11 automatically. |
| `unclear` | Judge cannot decide, OR (see below) its response could not be classified at all. |

A response that fails to parse, or names a verdict outside this table, is **not** a
crash: it lands in `unclear` with `parse_error=True`, matching `outcomes.classify`'s
rule that a failure is scored, never dropped. The exact aggregation arithmetic this
implements is fixed in `tests/fixtures/judge/HAND-COMPUTED.md`, written before this file,
and `tests/test_judge.py` checks it byte-for-byte against ten constructed raw responses
with no network call.

Blinding, and why it is fixed per item rather than per run
------------------------------------------------------------
The judge is never told which vendor produced which answer -- "Answer A" / "Answer B"
only. Which arm sits in which slot is decided by hashing the item's own merge key
(`_blind_a_is_incumbent`), not by a run-level coin flip, so re-running the same
disagreement set blinds identically every time and a diff between two judge runs is
comparable item for item. It also means a judge with a positional bias ("always prefers
Answer A") cannot be mistaken for a judge with a vendor bias, because which vendor holds
slot A varies item by item rather than being fixed for the whole run.

The judge model itself
-----------------------
Must not be either arm under evaluation -- a model judging its own family's output, or
its rival's, is not independent. Chosen for Experiment 6: `google/gemma-4-31b-it`,
reasoning disabled (`reasoning_effort="none"`), pinned to a single provider exactly like
every other arm in this repository, for the same reason: `EXPERIMENTS.md`'s Experiment 4
measured a 25-point swing on `reason` net from an endpoint change alone, and a probe
before writing this file found the judge itself is not immune -- the identical
temperature-0 request returned `ground_truth_correct` from CoreWeave and Novita and
`defensible_disagreement` from DeepInfra. One provider, recorded, or the judge's own
verdicts are not reproducible.

What this cannot do
--------------------
It cannot resolve a disagreement -- only flag one for a human, exactly as RET-11 was
originally found by a human and not by code. A judge that always said
`ground_truth_correct` would pass every test in this file trivially while being useless;
`tests/fixtures/judge/HAND-COMPUTED.md` says so explicitly, because the aggregation being
correct is not the same claim as the judge being any good. Whether this judge's opinions
carry real signal is an empirical question, answered in Experiment 6 against real
disagreement items, not asserted here.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from evalharness.compare import CoverageMismatch, comparison_clusters
from evalharness.records import Record
from evalgen.runtime import RuntimeSpec, build_runtime_request

__all__ = [
    "JUDGE_VERDICTS",
    "RULE_SOURCE_FILES",
    "JudgeError",
    "JudgeVerdict",
    "DisagreementItem",
    "find_disagreements",
    "parse_rule_citation",
    "resolve_rule_text",
    "build_judge_prompt",
    "judge_response_schema",
    "parse_judge_response",
    "summarize_judgments",
    "judgment_unit_id",
    "shareable_report",
    "run_judge",
]

JUDGE_VERDICTS: tuple[str, ...] = (
    "ground_truth_correct",
    "defensible_disagreement",
    "ground_truth_error",
    "unclear",
)

JUDGE_DIMENSIONS: tuple[str, ...] = ("call_result", "reason", "product")
_RESPONSE_KEYS = frozenset({"verdict", "cited_span", "rationale"})

# Evidence is deliberately a separate status from parse validity. A response can be
# perfectly valid JSON and still cite text that the transcript does not contain.
EVIDENCE_EXACT = "exact"
EVIDENCE_UNCHECKED = "unchecked"
EVIDENCE_NOT_REQUIRED = "not_required"
EVIDENCE_MISSING = "missing"
EVIDENCE_NOT_IN_TRANSCRIPT = "not_in_transcript"
EVIDENCE_INVALID_RESPONSE = "invalid_response"
EVIDENCE_NOT_EVALUATED = "not_evaluated"


class JudgeError(ValueError):
    """Raised on inputs the judge pipeline cannot make sense of.

    Never raised for a bad or unparseable MODEL response -- that is scored as
    `unclear, parse_error=True`, not an error. This is reserved for inputs the caller
    controls: an unknown dimension name, or a disagreement item whose merge key matches
    no item in the testset passed in.
    """


@dataclass(frozen=True)
class DisagreementItem:
    """One item where the ground truth and the two arms do not all three agree.

    `key` is the raw, UNHASHED `(call_id, phone, product)` merge key -- fine to hold in
    memory and to use for the deterministic blind-order hash, but never written to a
    committed file in this form. `run_judge` looks the item back up by this key and
    reports only `item_id` (already a public, documented synthetic identifier throughout
    this repository) in anything it returns.
    """

    key: tuple[str, str | None, str | None]
    dimension: str
    gt_label: str
    incumbent_label: str
    candidate_label: str
    population: str  # "both_wrong" | "incumbent_only_right" | "candidate_only_right"
    rule_labels: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class JudgeVerdict:
    """One locally validated judge response.

    `raw` remains available to the in-process caller. `run_judge` never places it in the
    ordinary report: callers that are allowed to retain raw model text must provide a
    separate `private_record_sink`, while the shareable record carries only its SHA.
    """

    verdict: str
    cited_span: str
    rationale: str
    parse_error: bool
    raw: str
    evidence_status: str = EVIDENCE_UNCHECKED
    validation_errors: tuple[str, ...] = ()
    execution_status: str = "completed"


def find_disagreements(
    gt: Sequence[Record],
    incumbent: Sequence[Record],
    candidate: Sequence[Record],
    dimension: str,
) -> list[DisagreementItem]:
    """Every non-both-right call cluster from the canonical paired population.

    The judge and the statistical verdict share ``comparison_clusters`` instead of
    maintaining two nearly-identical correctness implementations.  That keeps orphan
    false positives, invalid skeletons, product-set comparison, and call clustering
    identical on both paths.
    """
    if dimension not in JUDGE_DIMENSIONS:
        raise JudgeError(f"unknown dimension {dimension!r}")
    items: list[DisagreementItem] = []
    try:
        clusters = comparison_clusters(
            list(gt), list(incumbent), list(candidate), dimension
        )
    except (CoverageMismatch, ValueError) as exc:
        raise JudgeError(str(exc)) from exc
    for cluster in clusters:
        if cluster.incumbent_right and cluster.candidate_right:
            continue
        if not cluster.incumbent_right and not cluster.candidate_right:
            population = "both_wrong"
        elif cluster.incumbent_right:
            population = "incumbent_only_right"
        else:
            population = "candidate_only_right"
        items.append(
            DisagreementItem(
                key=cluster.source.key,
                dimension=dimension,
                gt_label=cluster.gt_label,
                incumbent_label=cluster.incumbent_label,
                candidate_label=cluster.candidate_label,
                population=population,
                rule_labels=cluster.rule_labels,
            )
        )
    return items


def _blind_a_is_incumbent(key: tuple[str, str | None, str | None]) -> bool:
    """Deterministic per item: which arm the judge sees as "Answer A". See module docstring."""
    digest = hashlib.sha256(repr(key).encode("utf-8")).digest()
    return digest[0] % 2 == 0


def judgment_unit_id(
    item_id: str, product: str | None, dimension: str
) -> str:
    """Return a stable, shareable id for one item x product x dimension judgment.

    The scorer's real key also contains call id and phone number. Neither belongs in a
    judge report. `item_id` is the already-shareable identifier supplied by the testset;
    product is included because one call can produce several scored rows, and dimension
    is included because each row can be adjudicated more than once for different claims.

    A dataset manifest/hash still has to scope this id globally. The digest only prevents
    punctuation and label text from becoming an accidental report schema; it is not a
    substitute for the repository's HMAC customer-key control.
    """
    if not isinstance(item_id, str) or not item_id.strip():
        raise JudgeError("item_id must be a non-empty string")
    if dimension not in JUDGE_DIMENSIONS:
        raise JudgeError(f"unknown dimension {dimension!r}")
    payload = json.dumps(
        {
            "item_id": item_id,
            "product": product,
            "dimension": dimension,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "ju_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _parse_rule_key(key: str, dimension: str) -> str | None:
    """`rule_<dimension>:<label>` or `rule_<dimension>:<label>#N` -> the label, else None.

    The `#N` forms are the packs' second-corroborating-citation keys (97 across the
    committed packs, 22 of them citing lines the base citation does not cover -- the
    2026-08-09 review found they were never matched, so RET-37 was quoted prompt.py:4342
    while the pack also cites :4345, the CRITICAL clause that settles it).
    """
    prefix = f"rule_{dimension}:"
    if not key.startswith(prefix):
        return None
    label = key[len(prefix):]
    if "#" in label:
        label = label.split("#", 1)[0]
    return label


def _rule_entries_for(
    rules: dict[str, str], dimension: str, labels: set[str]
) -> list[tuple[str, tuple[str, ...]]]:
    """Item-own citations per label in play -- base AND `#N` keys, deduped, ordered.

    A miss is not an error: `<empty>` and `<no prediction>` are placeholder labels with
    no rule to cite. But since the 2026-08-09 review, a miss here no longer means the
    label goes untexted -- `run_judge` falls back to the pack-level union, because this
    dict is authored per ground truth and quoting only its labels gave the judge the
    case for the reference label and nothing about the competing one (defect 4).
    """
    wanted = {label.lower(): label for label in labels}
    collected: dict[str, list[str]] = {}
    for key in sorted(rules):
        parsed = _parse_rule_key(key, dimension)
        if parsed is None or parsed.lower() not in wanted:
            continue
        bucket = collected.setdefault(parsed.lower(), [])
        if rules[key] not in bucket:
            bucket.append(rules[key])
    return [
        (wanted[lowered], tuple(citations))
        for lowered, citations in sorted(collected.items())
    ]


def _rule_citations_for(rules: dict[str, str], dimension: str, labels: set[str]) -> list[str]:
    """The `label: citation` pointer strings `build_judge_prompt` has always taken."""
    return [
        f"{label}: {citation}"
        for label, citations in _rule_entries_for(rules, dimension, labels)
        for citation in citations
    ]


def label_citation_union(items: Sequence[Any], dimension: str) -> dict[str, tuple[str, ...]]:
    """label -> every citation any item in the pack gives it, deduped, sorted.

    Built from the SAME testset the runs used, never another pack. This is the
    defect-4 fallback: a competing label the item's own ground-truth-authored `rules`
    dict never names still gets its rule text, from wherever in the pack that class IS
    asserted. Hand-enumerated expectation for two labels in the fixture addendum.
    """
    union: dict[str, set[str]] = {}
    for item in items:
        rules = getattr(item, "rules", None) or {}
        for key, citation in rules.items():
            label = _parse_rule_key(key, dimension)
            if label is None:
                continue
            union.setdefault(label.lower(), set()).add(citation)
    return {label: tuple(sorted(cites)) for label, cites in union.items()}


def resolve_citations_dedup(
    citations: Sequence[str],
    root: Path,
    _cache: dict[str, list[str] | None] | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve several citations into one line list, deduped by (file, line number).

    The fixture's union example is why the dedup key is the line's identity and not its
    text: `prompt.py:4368` and `prompt.py:4369` sit inside `prompt.py:4367-4369`, and a
    label whose union carries all three citations must quote each line once.
    """
    cache = _cache if _cache is not None else {}
    seen: set[tuple[str, int]] = set()
    lines: list[str] = []
    unresolved: list[str] = []
    for citation in citations:
        parsed, unparsed = parse_rule_citation(citation)
        unresolved.extend(f"{fragment} (no file:line form)" for fragment in unparsed)
        for basename, first, last in parsed:
            relative = RULE_SOURCE_FILES.get(basename)
            if relative is None:
                unresolved.append(f"{basename}:{first} (unmapped file)")
                continue
            if basename not in cache:
                try:
                    cache[basename] = (root / relative).read_text(
                        encoding="utf-8"
                    ).splitlines()
                except OSError:
                    cache[basename] = None
            file_lines = cache[basename]
            if file_lines is None:
                # NOT `under {root}`: that is an absolute local path carrying the
                # operator's OS account name, and this string travels twice -- into the
                # prompt via `_format_rule_entry`, and into `rule_text.unresolved_fragments`
                # in every shareable export, where dropping `rule_text.root` alone did not
                # remove it. `assert_shareable_payload` does not reject a path.
                # Review finding, 2026-08-09; the fragment stays actionable without it.
                unresolved.append(f"{basename}:{first} (file unreadable under the rule-source root)")
                continue
            if first < 1 or last > len(file_lines):
                unresolved.append(f"{basename}:{first}-{last} (outside 1-{len(file_lines)})")
                continue
            for lineno in range(first, last + 1):
                if (basename, lineno) in seen:
                    continue
                seen.add((basename, lineno))
                lines.append(file_lines[lineno - 1])
    return lines, unresolved


# ------------------------------------------------------------- rule-text resolution
#
# Why this exists: Experiment 6's cross-validated "possible ground-truth error" flags
# were hand-checked on 2026-08-08 and three of four were judge errors with one root
# cause -- the judge received `customer reason: prompt.py:4372` but never line 4372's
# text, so it re-derived class boundaries from common sense, and this vocabulary is
# deliberately counterintuitive exactly where it matters (indecision counts as `save`,
# prompt.py:4397; refusing to give a reason IS `customer reason`, prompt.py:4372;
# `undefined` means out-of-scope, not unresolved, prompt.py:4399). The arithmetic below
# is fixed by hand in tests/fixtures/judge/HAND-COMPUTED.md, addendum of 2026-08-09,
# written before this code.

# Basename -> path relative to the rule-source root. The root is `production-reference/`
# (the PARENT of both apps) because `prompt.txt` belongs to the MNP sibling, not the
# retention tree (tests/fixtures/testsets/README.md).
RULE_SOURCE_FILES: Mapping[str, str] = {
    "prompt.py": "sentiment-batch-retention-main/src/prompt.py",
    "main.py": "sentiment-batch-retention-main/src/main.py",
    "fact_checker.py": "sentiment-batch-retention-main/src/modules/fact_checker.py",
    "prompt.txt": "sentiment-batch-mnp-develop/config/system_prompt/prompt.txt",
}

_CITATION_FRAGMENT = re.compile(
    r"^(?P<file>[A-Za-z_][\w.]*\.(?:py|txt)):(?P<first>\d+)(?:-(?P<last>\d+))?$"
)


def parse_rule_citation(citation: str) -> tuple[list[tuple[str, int, int]], list[str]]:
    """Split a citation on `;`/`,` into `(file, first, last)` spans plus the leftovers.

    Leftovers are returned, never dropped: `grain` and `merge` are real citation values
    in the packs (mechanics, not file:line), and a fragment that fails to parse must
    surface as unresolved rather than silently vanish -- the same rule
    `outcomes.classify` applies to failures.
    """
    parsed: list[tuple[str, int, int]] = []
    unparsed: list[str] = []
    for fragment in re.split(r"[;,]", citation):
        fragment = fragment.strip()
        if not fragment:
            continue
        match = _CITATION_FRAGMENT.match(fragment)
        if match is None:
            unparsed.append(fragment)
            continue
        first = int(match.group("first"))
        last = int(match.group("last") or first)
        if last < first:
            unparsed.append(fragment)
            continue
        parsed.append((match.group("file"), first, last))
    return parsed, unparsed


def resolve_rule_text(
    citation: str,
    root: Path,
    _cache: dict[str, list[str] | None] | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve one citation to the cited source lines, reporting what did not resolve.

    Returns `(lines, unresolved)`. Lines keep their leading indentation and lose only
    the trailing newline, exactly as the hand-computed fixture copies them. Every
    fragment that cannot be turned into text -- unparsable, unmapped basename, missing
    file, out-of-range line -- lands in `unresolved` with the reason attached.
    """
    cache = _cache if _cache is not None else {}
    parsed, unresolved = parse_rule_citation(citation)
    unresolved = [f"{fragment} (no file:line form)" for fragment in unresolved]
    lines: list[str] = []
    for basename, first, last in parsed:
        relative = RULE_SOURCE_FILES.get(basename)
        if relative is None:
            unresolved.append(f"{basename}:{first} (unmapped file)")
            continue
        if basename not in cache:
            path = root / relative
            try:
                cache[basename] = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                cache[basename] = None
        file_lines = cache[basename]
        if file_lines is None:
            # See `resolve_citations_dedup`: the root is an absolute local path and this
            # fragment reaches both the prompt and the shareable export.
            unresolved.append(f"{basename}:{first} (file unreadable under the rule-source root)")
            continue
        if first < 1 or last > len(file_lines):
            unresolved.append(
                f"{basename}:{first}-{last} (outside 1-{len(file_lines)})"
            )
            continue
        lines.extend(file_lines[first - 1 : last])
    return lines, unresolved


def _format_rule_entry(
    label: str, citation: str, lines: Sequence[str], unresolved: Sequence[str]
) -> str:
    """One prompt block: the pointer line, then the quoted text, then loud gaps."""
    parts = [f"- {label} ({citation}):"]
    parts.extend(f"    > {line}" for line in lines)
    parts.extend(
        f"    [text unavailable for {fragment} -- pointer only, do not invent it]"
        for fragment in unresolved
    )
    if not lines and not unresolved:
        parts.append("    [no text resolved -- pointer only, do not invent it]")
    return "\n".join(parts)


def build_judge_prompt(
    transcript: str,
    item: DisagreementItem,
    rule_citations: Sequence[str],
    rule_texts: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    """The messages sent to the judge. Neither arm is named; see module docstring.

    `rule_texts` is the resolved-rule-text path: pre-formatted blocks from
    `_format_rule_entry`, one per rule entry, quoting the cited production lines
    verbatim. Without it (the default) the prompt is byte-identical to the
    pointer-only prompt every earlier run sent, so no existing caller changes
    behavior by accident -- the hand-computed fixture addendum pins that.

    Why the text matters enough to change the prompt: this vocabulary is deliberately
    counterintuitive exactly where disagreements concentrate. A judge shown only
    `customer reason: prompt.py:4372` re-derives the class boundary from common sense
    and gets it backwards; a judge shown line 4372 itself can apply it as written.
    """
    a_is_incumbent = _blind_a_is_incumbent(item.key)
    answer_a = item.incumbent_label if a_is_incumbent else item.candidate_label
    answer_b = item.candidate_label if a_is_incumbent else item.incumbent_label
    # `is not None`, never truthiness: `bool([])` was the silent-placebo path the
    # 2026-08-09 review found -- 8 units whose labels had no rule key were sent a
    # byte-identical pointer request while the report said rule text was enabled.
    with_text = rule_texts is not None
    if with_text:
        rules_heading = "Production rule text, quoted verbatim from the production source"
        rules_block = "\n".join(rule_texts) or "(none on file)"
        rules_stance = (
            "Where a rule's text is quoted under its reference, apply it exactly as "
            "written, even where it contradicts your intuition -- these class "
            "definitions are deliberate and sometimes counterintuitive. Where a line "
            "is marked text-unavailable, treat it as a pointer only and do not invent "
            "its contents. "
        )
    else:
        rules_heading = "Production rule reference(s) (not rule text)"
        rules_block = "\n".join(f"- {c}" for c in rule_citations) or "(none on file)"
        rules_stance = (
            "File-and-line rule references are provenance pointers, not rule text; do not "
            "invent their contents. "
        )
    system = (
        "You are an independent adjudicator reviewing a disputed label on a call-"
        "transcript labelling task. You did not produce either answer being compared "
        "and have no stake in which one is right. Given a transcript, the production "
        "available rule references, the pack's reference label under review, and two "
        "models' actual answers (blinded as Answer A and Answer B, in no particular "
        "order), decide exactly one of:\n"
        "ground_truth_correct -- the compared answer(s) are simply wrong, ground truth "
        "holds.\n"
        "defensible_disagreement -- at least one compared answer is also reasonable "
        "given the transcript, alongside the ground truth.\n"
        "ground_truth_error -- a compared answer is clearly the better reading and the "
        "ground truth itself looks wrong.\n"
        "unclear -- the transcript does not give you enough to decide.\n"
        "Do not assume the reference label is correct because it is named as the "
        "reference. First derive the best label from the transcript itself, then "
        "compare that conclusion with the reference and the two blinded answers. "
        + rules_stance +
        "Quote the exact transcript span your rationale rests on, verbatim, in "
        "cited_span. Respond with the JSON schema only, no other text."
    )
    user = (
        f"Dimension: {item.dimension}\n\n"
        f"Transcript:\n{transcript}\n\n"
        f"{rules_heading}:\n{rules_block}\n\n"
        f"Reference label under review: {item.gt_label}\n"
        f"Answer A: {answer_a}\n"
        f"Answer B: {answer_b}\n\n"
        "Adjudicate."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def judge_response_schema() -> dict[str, Any]:
    """The `response_format` sent with every judge call. `strict: True` throughout."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "judge_verdict",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": list(JUDGE_VERDICTS)},
                    "cited_span": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["verdict", "cited_span", "rationale"],
                "additionalProperties": False,
            },
        },
    }


def _invalid_response(
    raw_text: str,
    errors: Sequence[str],
    *,
    cited_span: str = "",
    rationale: str = "",
    evidence_status: str = EVIDENCE_INVALID_RESPONSE,
) -> JudgeVerdict:
    """One unusable model response, preserved rather than raised or dropped."""
    return JudgeVerdict(
        verdict="unclear",
        cited_span=cited_span,
        rationale=rationale,
        parse_error=True,
        raw=raw_text,
        evidence_status=evidence_status,
        validation_errors=tuple(errors),
    )


def parse_judge_response(
    raw_text: str, *, transcript: str | None = None
) -> JudgeVerdict:
    """Parse and locally validate one raw response; never raise on model text.

    The remote `strict: True` schema is a request, not evidence that the endpoint honoured
    it. This function independently enforces the exact key set, string field types, a
    non-empty rationale, and a non-empty cited span for every decisive verdict. `unclear`
    may omit a span because "the transcript is insufficient" can itself be the result.

    When `transcript` is provided, every non-empty citation must occur byte-for-byte in
    it. `run_judge` always provides the transcript. Keeping the parameter optional
    preserves the pure parsing/aggregation fixture, whose job is deliberately narrower
    than evidence validation.
    """
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return _invalid_response(raw_text, ("response is not valid JSON",))
    if not isinstance(payload, dict):
        return _invalid_response(raw_text, ("response root is not an object",))

    errors: list[str] = []
    keys = set(payload)
    missing = sorted(_RESPONSE_KEYS - keys)
    extra = sorted(keys - _RESPONSE_KEYS)
    if missing:
        errors.append(f"missing required field(s): {missing}")
    if extra:
        errors.append(f"unexpected field(s): {extra}")

    verdict = payload.get("verdict")
    cited_value = payload.get("cited_span")
    rationale_value = payload.get("rationale")

    if not isinstance(verdict, str):
        errors.append("verdict must be a string")
    if verdict not in JUDGE_VERDICTS:
        errors.append(f"verdict is not one of {list(JUDGE_VERDICTS)}")
    if not isinstance(cited_value, str):
        errors.append("cited_span must be a string")
    if not isinstance(rationale_value, str):
        errors.append("rationale must be a string")

    cited_span = cited_value if isinstance(cited_value, str) else ""
    rationale = rationale_value if isinstance(rationale_value, str) else ""
    if not rationale.strip():
        errors.append("rationale must be non-empty")

    evidence_status = EVIDENCE_UNCHECKED
    if isinstance(verdict, str) and verdict != "unclear" and not cited_span.strip():
        evidence_status = EVIDENCE_MISSING
        errors.append("decisive verdicts require a non-empty cited_span")
    elif not cited_span.strip():
        evidence_status = EVIDENCE_NOT_REQUIRED
    elif transcript is None:
        evidence_status = EVIDENCE_UNCHECKED
    elif cited_span in transcript:
        evidence_status = EVIDENCE_EXACT
    else:
        evidence_status = EVIDENCE_NOT_IN_TRANSCRIPT
        errors.append("cited_span is not an exact substring of the transcript")

    if errors:
        return _invalid_response(
            raw_text,
            errors,
            cited_span=cited_span,
            rationale=rationale,
            evidence_status=evidence_status,
        )

    return JudgeVerdict(
        verdict=verdict,
        cited_span=cited_span,
        rationale=rationale,
        parse_error=False,
        raw=raw_text,
        evidence_status=evidence_status,
    )


def summarize_judgments(verdicts: Sequence[JudgeVerdict]) -> dict[str, Any]:
    """Pure arithmetic. See `tests/fixtures/judge/HAND-COMPUTED.md` for the fixed
    expectation this reproduces exactly, n=10, before this function existed.

    `0.0` rather than `nan` on an empty input, matching `evidence._with_rates`: a run
    that adjudicated nothing has no rate, and `nan` prints as a defect wherever it lands.
    """
    total = len(verdicts)
    counts = {v: 0 for v in JUDGE_VERDICTS}
    all_response_counts = {v: 0 for v in JUDGE_VERDICTS}
    parse_errors = 0
    transport_errors = 0
    for verdict in verdicts:
        all_response_counts[verdict.verdict] += 1
        if not verdict.parse_error and verdict.execution_status == "completed":
            counts[verdict.verdict] += 1
        if verdict.parse_error:
            parse_errors += 1
        if verdict.execution_status == "transport_error":
            transport_errors += 1
    execution_errors = sum(
        verdict.execution_status != "completed" for verdict in verdicts
    )
    usable = sum(
        not verdict.parse_error and verdict.execution_status == "completed"
        for verdict in verdicts
    )
    rates = {
        f"{key}_rate": (counts[key] / usable if usable else 0.0)
        for key in JUDGE_VERDICTS
    }
    return {
        "total": total,
        "counts": counts,
        "all_response_counts": all_response_counts,
        **rates,
        "parse_error_count": parse_errors,
        "parse_error_rate": (parse_errors / total if total else 0.0),
        "transport_error_count": transport_errors,
        "transport_error_rate": (transport_errors / total if total else 0.0),
        "execution_error_count": execution_errors,
        "execution_error_rate": (execution_errors / total if total else 0.0),
        "usable_count": usable,
        "usable_rate": (usable / total if total else 0.0),
    }


def _normalise_dimensions(dimensions: Sequence[str]) -> list[str]:
    """Validate and de-duplicate dimensions without changing first-seen order."""
    if isinstance(dimensions, str):
        dimensions = [dimensions]
    result: list[str] = []
    seen: set[str] = set()
    for dimension in dimensions:
        if dimension not in JUDGE_DIMENSIONS:
            raise JudgeError(f"unknown dimension {dimension!r}")
        if dimension not in seen:
            result.append(dimension)
            seen.add(dimension)
    if not result:
        raise JudgeError("at least one judge dimension is required")
    return result


def _json_sha(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_SHAREABLE_ITEM_FIELDS = (
    "judgment_unit_id",
    "dimension",
    "population",
    "verdict",
    "parse_error",
    "evidence_status",
    "validation_errors",
    "execution_status",
    "identity_status",
    "transport_error_type",
    "observed_model",
    "observed_provider",
    "reasoning_tokens",
    "cost",
    "latency_s",
    "request_sha256",
    "raw_response_sha256",
    "rule_text_resolved",
    "rule_text_unresolved",
    "replicate",
)


def shareable_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return the report surface safe for a no-transcript aggregate artifact.

    The ordinary review records intentionally retain `cited_span` and `rationale` so a
    human can inspect a flag. Those fields can contain transcript text and therefore do
    not belong in a broadly shareable export. Raw response text is never present in the
    report at all; it can only leave `run_judge` through `private_record_sink`.
    """
    top_level = (
        "schema_version",
        "model_requested",
        "provider_requested",
        "dimensions",
        "candidates_found",
        "summary",
        "truncated",
        "source_provenance",
        "judge_runtime",
        "incumbent_arm",
        "candidate_arm",
        "rule_text",
        "repeats",
        "units",
    )
    safe = {key: report[key] for key in top_level if key in report}
    # `rule_text.root` is an absolute local path and carries the operator's OS account
    # name. The counts are the shareable part; where the tree sat on one workstation is
    # not, and `assert_shareable_payload` does not reject a path. Dropped here rather
    # than at the call site so every export gets it (review finding, 2026-08-09).
    if isinstance(safe.get("rule_text"), Mapping):
        safe["rule_text"] = {
            key: value for key, value in safe["rule_text"].items() if key != "root"
        }
    safe["items"] = [
        {key: row.get(key) for key in _SHAREABLE_ITEM_FIELDS if key in row}
        for row in report.get("items", [])
    ]
    return safe


def run_judge(
    *,
    testset: Any,
    gt: Sequence[Record],
    incumbent: Sequence[Record],
    candidate: Sequence[Record],
    dimensions: Sequence[str],
    client: Any,
    model: str,
    provider: str | None,
    reasoning_effort: str = "none",
    max_items: int | None = None,
    source_provenance: Mapping[str, Any] | None = None,
    private_record_sink: Callable[[Mapping[str, Any]], None] | None = None,
    rule_source_root: str | Path | None = None,
    repeats: int = 1,
) -> dict[str, Any]:
    """Call the judge `repeats` times per disagreement unit, return a report.

    `repeats` exists because the 2026-08-09 review measured the judge flipping its
    verdict on 4 of 8 byte-identical requests at temperature 0 -- single-shot judge
    deltas are not separable from resampling noise. Unit-level aggregation is a strict
    majority; a tie is `no_majority`, never broken by picking, per the hand-computed
    table in the fixture's second addendum. A unit counts as flagged only on a
    strict-majority `ground_truth_error`.

    `max_items` truncates deterministically (first N in testset order) rather than
    sampling, and the caller is responsible for logging what was dropped -- silent
    truncation reads as "covered everything" when it did not.

    The returned `items` are review records: their rationale and cited span may quote the
    transcript. `shareable_report(result)` removes those fields. Raw response text and the
    exact request never enter the result at all; an authorized caller can retain them in a
    separate restricted store by passing `private_record_sink`.

    `source_provenance` is an optional block of already-shareable hashes/ids supplied by
    the caller (for example run ids, replicate, testset sha and ground-truth sha). This
    module cannot derive those values from the in-memory Records it receives.

    `rule_source_root` turns on rule-text resolution: each cited `file:line` is read out
    of that tree (`RULE_SOURCE_FILES` maps basenames; the root is `production-reference/`)
    and quoted verbatim in the prompt. A root that does not exist raises, because the
    caller explicitly asked for text and a silent pointer-only downgrade is how the
    2026-08-08 false flags happened in the first place. Fragments that fail to resolve
    are counted in the report and marked in the prompt, never dropped.
    """
    if max_items is not None and (
        isinstance(max_items, bool) or not isinstance(max_items, int) or max_items < 0
    ):
        raise JudgeError("max_items must be a non-negative integer or None")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
        raise JudgeError("repeats must be a positive integer")
    rule_root: Path | None = None
    if rule_source_root is not None:
        rule_root = Path(rule_source_root)
        if not rule_root.is_dir():
            raise JudgeError(
                f"rule_source_root {rule_root} is not a directory. Rule-text resolution "
                "was explicitly requested; refusing to fall back to pointer-only "
                "prompts silently."
            )
    rule_file_cache: dict[str, list[str] | None] = {}
    rule_parts_resolved = 0
    rule_parts_unresolved = 0
    rule_unresolved_fragments: set[str] = set()
    units_without_rule_text: list[dict[str, Any]] = []
    normalised_dimensions = _normalise_dimensions(dimensions)
    dimension_unions: dict[str, dict[str, tuple[str, ...]]] = (
        {
            dimension: label_citation_union(testset.items, dimension)
            for dimension in normalised_dimensions
        }
        if rule_root is not None
        else {}
    )

    by_key: dict[tuple[str, str], Any] = {}
    for test_item in testset.items:
        test_key = (str(test_item.call_id), str(test_item.phone_number))
        if test_key in by_key:
            raise JudgeError(
                f"testset has duplicate call/phone lookup key {test_key}; a judge result "
                "could not identify which transcript it reviewed"
            )
        by_key[test_key] = test_item

    all_items: list[DisagreementItem] = []
    seen_units: set[tuple[tuple[str, str | None, str | None], str]] = set()
    for dimension in normalised_dimensions:
        for disagreement_item in find_disagreements(gt, incumbent, candidate, dimension):
            unit = (disagreement_item.key, disagreement_item.dimension)
            if unit in seen_units:
                raise JudgeError(
                    f"duplicate judgment unit {unit}; duplicated ground-truth keys must "
                    "be resolved before model calls"
                )
            seen_units.add(unit)
            all_items.append(disagreement_item)
    candidates_found = len(all_items)
    truncated = max_items is not None and candidates_found > max_items
    if max_items is not None:
        all_items = all_items[:max_items]

    records: list[dict[str, Any]] = []
    verdicts: list[JudgeVerdict] = []
    for disagreement_item in all_items:
        lookup_key = (str(disagreement_item.key[0]), str(disagreement_item.key[1]))
        item = by_key.get(lookup_key)
        if item is None:
            raise JudgeError(
                f"disagreement key {disagreement_item.key} matches no item in the "
                "testset passed in. Pass the same testset the runs being compared used."
            )
        labels: set[str] = set(disagreement_item.rule_labels)
        if not labels:  # compatibility for directly constructed diagnostic items
            for label in (
                disagreement_item.gt_label,
                disagreement_item.incumbent_label,
                disagreement_item.candidate_label,
            ):
                if label in ("<empty>", "<no prediction>", "<no ground truth>"):
                    continue
                if disagreement_item.dimension in {"reason", "product"}:
                    labels.update(part.strip() for part in label.split(","))
                else:
                    labels.add(label)
        own_entries = _rule_entries_for(item.rules, disagreement_item.dimension, labels)
        own_by_label = {label.lower(): citations for label, citations in own_entries}
        citations = [
            f"{label}: {citation}"
            for label, cites in own_entries
            for citation in cites
        ]
        rule_texts: list[str] | None = None
        record_resolved = 0
        record_unresolved = 0
        if rule_root is not None:
            # Defect-4 fix: every label in play gets rule text -- the item's own
            # citations (base + #N keys) when it has them, else the pack-level union,
            # else a VISIBLE no-text line. Never a silent fallback to pointer mode.
            union = dimension_unions[disagreement_item.dimension]
            rule_texts = []
            for label in sorted(labels):
                label_citations = own_by_label.get(label.lower()) or union.get(
                    label.lower(), ()
                )
                if not label_citations:
                    rule_texts.append(
                        f"- {label}: [no rule text on file for this label anywhere in "
                        "the pack -- do not invent one]"
                    )
                    continue
                lines, unresolved = resolve_citations_dedup(
                    label_citations, rule_root, _cache=rule_file_cache
                )
                rule_texts.append(
                    _format_rule_entry(
                        label, "; ".join(label_citations), lines, unresolved
                    )
                )
                record_resolved += len(lines)
                record_unresolved += len(unresolved)
                rule_unresolved_fragments.update(unresolved)
            rule_parts_resolved += record_resolved
            rule_parts_unresolved += record_unresolved
            if record_resolved == 0:
                units_without_rule_text.append(
                    {
                        "item_id": item.item_id,
                        "dimension": disagreement_item.dimension,
                        "labels": sorted(labels),
                    }
                )
        messages = build_judge_prompt(
            item.transcript_th, disagreement_item, citations, rule_texts=rule_texts
        )
        # The literal request dict is both sent and hashed. A separately reconstructed
        # provenance object would eventually drift from what the endpoint saw.
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": 1000,
            "temperature": 0.0,
            "top_p": 1.0,
            "seed": 0,
            "response_format": judge_response_schema(),
            "provider": provider,
            "reasoning_effort": reasoning_effort,
        }
        runtime = getattr(client, "runtime", None)
        wire_request = (
            build_runtime_request(runtime, **request)
            if isinstance(runtime, RuntimeSpec)
            else request
        )
        request_sha = _json_sha(wire_request)
        unit_id = judgment_unit_id(
            item.item_id,
            disagreement_item.key[2],
            disagreement_item.dimension,
        )

        base_record = {
            "judgment_unit_id": unit_id,
            "item_id": item.item_id,
            "product": disagreement_item.key[2],
            "dimension": disagreement_item.dimension,
            "population": disagreement_item.population,
            "gt_label": disagreement_item.gt_label,
            "incumbent_label": disagreement_item.incumbent_label,
            "candidate_label": disagreement_item.candidate_label,
            "request_sha256": request_sha,
            "rule_text_resolved": record_resolved if rule_root is not None else None,
            "rule_text_unresolved": record_unresolved if rule_root is not None else None,
        }
        for replicate in range(1, repeats + 1):
            try:
                completion = client.complete(**request)
            except Exception as exc:  # real client TransportError carries latency_s
                if not hasattr(exc, "latency_s"):
                    # A programming error in a client implementation is not a transport
                    # outcome and must not be laundered into a plausible judge record.
                    raise
                verdict = JudgeVerdict(
                    verdict="unclear",
                    cited_span="",
                    rationale="",
                    parse_error=False,
                    raw="",
                    evidence_status=EVIDENCE_NOT_EVALUATED,
                    execution_status="transport_error",
                )
                verdicts.append(verdict)
                private = {
                    "judgment_unit_id": unit_id,
                    "replicate": replicate,
                    "request": wire_request,
                    "raw_response_text": None,
                    "completion_raw": None,
                    "transport_error": f"{type(exc).__name__}: {exc}",
                }
                if private_record_sink is not None:
                    private_record_sink(private)
                records.append(
                    {
                        **base_record,
                        "replicate": replicate,
                        "verdict": verdict.verdict,
                        "cited_span": "",
                        "rationale": "",
                        "parse_error": False,
                        "evidence_status": verdict.evidence_status,
                        "validation_errors": [],
                        "execution_status": verdict.execution_status,
                        "identity_status": "not_evaluated",
                        "transport_error_type": type(exc).__name__,
                        "observed_model": None,
                        "observed_provider": None,
                        "reasoning_tokens": None,
                        "cost": None,
                        "latency_s": getattr(exc, "latency_s"),
                        "raw_response_sha256": None,
                    }
                )
                continue

            raw_text = completion.content or ""
            verdict = parse_judge_response(raw_text, transcript=item.transcript_th)
            verdicts.append(verdict)
            observed_model = getattr(completion, "observed_model", None)
            observed_provider = getattr(completion, "provider", None)
            provider_matches = provider is None or observed_provider == provider
            if observed_model == model and provider_matches:
                identity_status = "matched"
            elif observed_model is None or (
                provider is not None and observed_provider is None
            ):
                identity_status = "unverified"
            else:
                identity_status = "mismatch"
            if identity_status != "matched":
                verdict = replace(verdict, execution_status=f"identity_{identity_status}")
                verdicts[-1] = verdict

            private = {
                "judgment_unit_id": unit_id,
                "replicate": replicate,
                "request": wire_request,
                "raw_response_text": raw_text,
                "completion_raw": getattr(completion, "raw", None),
                "transport_error": None,
            }
            if private_record_sink is not None:
                private_record_sink(private)
            records.append(
                {
                    **base_record,
                    "replicate": replicate,
                    "verdict": verdict.verdict,
                    "cited_span": verdict.cited_span,
                    "rationale": verdict.rationale,
                    "parse_error": verdict.parse_error,
                    "evidence_status": verdict.evidence_status,
                    "validation_errors": list(verdict.validation_errors),
                    "execution_status": verdict.execution_status,
                    "identity_status": identity_status,
                    "transport_error_type": None,
                    "observed_model": observed_model,
                    "observed_provider": observed_provider,
                    "reasoning_tokens": getattr(completion, "reasoning_tokens", None),
                    "cost": getattr(completion, "cost", None),
                    "latency_s": getattr(completion, "latency_s", None),
                    "raw_response_sha256": hashlib.sha256(
                        raw_text.encode("utf-8")
                    ).hexdigest(),
                }
            )

    runtime = getattr(client, "runtime", None)
    judge_runtime = (
        {
            "manifest": runtime.manifest(),
            "fingerprint": runtime.fingerprint(),
        }
        if isinstance(runtime, RuntimeSpec)
        else None
    )
    report = {
        "schema_version": 2,
        "model_requested": model,
        "provider_requested": provider,
        "dimensions": normalised_dimensions,
        "candidates_found": candidates_found,
        "items": records,
        "summary": summarize_judgments(verdicts),
        "truncated": truncated,
        "source_provenance": dict(source_provenance or {}),
        "judge_runtime": judge_runtime,
        "repeats": repeats,
        "units": _unit_aggregation(records),
        # Loud, not buried in prompts: a partially-broken resolution shows up here as a
        # nonzero unresolved count, and a run that never asked for text says so.
        "rule_text": {
            "enabled": rule_root is not None,
            "root": str(rule_root) if rule_root is not None else None,
            "resolved_parts": rule_parts_resolved,
            "unresolved_parts": rule_parts_unresolved,
            "unresolved_fragments": sorted(rule_unresolved_fragments),
            "units_without_rule_text": units_without_rule_text,
        },
    }
    return report


def _unit_aggregation(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Majority-per-unit arithmetic, fixed by hand in the fixture's second addendum.

    Strict majority only (`top count * 2 > replicate count`); a tie is `no_majority`,
    never broken by picking. A unit is flagged only on a strict-majority
    `ground_truth_error`, so single-replicate noise cannot put an item on the human
    review queue by itself once `repeats > 1`.
    """
    by_unit: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        by_unit.setdefault(record["judgment_unit_id"], []).append(record)
    flagged: list[dict[str, Any]] = []
    flipped = 0
    no_majority = 0
    for unit_id, unit_records in by_unit.items():
        verdicts = [
            r["verdict"]
            for r in sorted(unit_records, key=lambda r: r.get("replicate") or 1)
        ]
        counts: dict[str, int] = {}
        for v in verdicts:
            counts[v] = counts.get(v, 0) + 1
        top_verdict, top_count = max(counts.items(), key=lambda kv: kv[1])
        if len(set(verdicts)) > 1:
            flipped += 1
        if top_count * 2 <= len(verdicts):
            no_majority += 1
            continue
        if top_verdict == "ground_truth_error":
            first = unit_records[0]
            flagged.append(
                {
                    "judgment_unit_id": unit_id,
                    "item_id": first["item_id"],
                    "dimension": first["dimension"],
                    "votes": f"{top_count}/{len(verdicts)}",
                }
            )
    return {
        "total": len(by_unit),
        "flagged_majority": len(flagged),
        "flagged": sorted(flagged, key=lambda f: (f["item_id"], f["dimension"])),
        "flipped": flipped,
        "no_majority": no_majority,
    }
