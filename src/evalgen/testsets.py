"""The synthetic testset: load it, hash it, and refuse it when a label has drifted.

A testset item is a claim, not a sample. It says: *this* transcript, *these* labels,
and here is why anyone else would reach the same labels. That "why" is two fields, and
both are mandatory (`tests/fixtures/testsets/VOCABULARIES.md`):

  * `ev_<dimension>:<label>` — a **verbatim substring of that item's own transcript**.
  * `rule_<dimension>:<label>` — a `file:line` citation of the production line that
    makes that span decisive.

If no production line makes the label follow from the span, the item is under-specified
and **the transcript gets rewritten**. The label is never argued into place.

**The evidence check is the reason this module exists.** Everything else here is
hygiene. A ground-truth label whose evidence span no longer appears in its transcript
is a label that has drifted away from the text it claims to describe, and drift of that
kind is invisible: the file still parses, the tests still run, the numbers still print,
and the testset has quietly stopped measuring what it says it measures. `validate`
turns that into a failure with an item id attached.

Scope
-----
One file is one app. MNP, RTR, Sentiment QA and Telesales reuse several of these label
names with different meanings, so a citation does not carry across apps
(`VOCABULARIES.md`, "Scope"). Hence `app` on the TestSet rather than on the item.

Grain
-----
`gt` is a tuple of rows, one per product mentioned in the call, because the scored row
is `(call_id, phone_number, product)` and not the call (`fact_checker.py:1075`). The
five columns are fixed: `product`, `call_result`, `main`, `secondary`, `third`.

Three things about those columns that shape what `validate` can honestly check:

  1. **The outcome column is named `call_result`, not `retention_outcome`.** The model
     emits `retention_outcome`; `fact_checker.py:622` renames it on the way into the
     scorer. Same label, two names.
  2. **Reason cells are comma-split** (`fact_checker.py:877`), so a cell reading
     `"network, save cost"` is two labels and needs two of everything. This module
     splits the same way, which is also why the prompt-only 12th reason
     (`true point, dtac reward`) is structurally unscorable — it splits into two
     labels that are in no label space (**D1**, **D10**).
  3. **Rank is discarded** (`fact_checker.py:873-878`): `main`/`secondary`/`third` are
     unioned into a set. An item whose only distinguishing feature is rank tests
     nothing (**D9**), and no check here can rescue it.

`issue_type`, `churn_probability` and `call_event_detection` are not scored dimensions
(`fact_checker.py:1095-1099`) and so have no `gt` column. They may still appear in
`evidence` and `rules` as documentation — and an evidence span for one of them is held
to the same verbatim standard as any other, because a documented span that is not in
the transcript is just as wrong.

No client, no network, by construction: this module builds and checks files. The
generator half of `evalgen` is what talks to models.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import re
from dataclasses import dataclass, fields
from pathlib import Path

from evalharness.labelspaces import MNP, RETENTION, REASON_COLUMNS, LabelSpace

__all__ = [
    "CALL_ID_PATTERN",
    "GT_COLUMNS",
    "PHONE_PATTERN",
    "TestItem",
    "TestSet",
    "TestsetError",
    "load_testset",
    "testset_sha",
    "validate",
]


class TestsetError(ValueError):
    """Raised when a testset file cannot be read as a testset at all.

    Structural failures raise; label failures are returned by `validate`. The split is
    deliberate: a file that will not parse has no item ids to report problems against,
    and returning `["the file is broken"]` from a function whose contract is "a list of
    per-item problems" would be a lie about what was checked.
    """

    # This package's public names collide with pytest's collection rules: anything
    # called Test* or test* that is merely IMPORTED into a test module gets collected.
    # `TestItem` and `TestSet` would be gathered as test classes and `testset_sha` as a
    # test function, which shows up as a confusing error in a file that did nothing
    # wrong. Marking the objects is better than renaming them: the names describe what
    # they are, and every future test file gets the fix for free.
    __test__ = False


# Synthetic identifiers, enforced rather than trusted. NO real customer data reaches
# this repository: call ids live in 5000-5999 and phone numbers in the 0810000xxx
# block, both outside anything True's systems issue. `src/evalharness/paths.py` refuses
# a data directory inside a git worktree for the same reason; this is that refusal
# applied to the one dataset that IS committed.
#
# WIDENED 2026-08-06, `^08100000[0-9]{2}$` -> `^0810000[0-9]{3}$`, because the old block
# was FULL. MEASURED across `tests/fixtures/testsets/`: 100 distinct phone numbers in
# use, which is all 100 that `08100000xx` can spell, and `retention_v2.jsonl` alone
# accounts for every one of them. The next item added had nowhere to draw a phone from.
# Capacity is now 1000, so 900 are free.
#
# Why this widening and not some other one. Each point below was checked by running it,
# not by reading the regex:
#
#   * **It is a strict superset, so no fixture moves.** `0810000` + `001` is the same
#     string as `08100000` + `01`: the change drops one fixed digit from the prefix and
#     lengthens the tail to match, rather than sliding the block somewhere new. All 100
#     numbers already in use still match, and `validate()` returns an IDENTICAL problem
#     list — empty, on both — for `retention_v1.jsonl` and `retention_v2.jsonl` under
#     the old pattern and the new one. Those two files are frozen, four experiments
#     cite them, and that is the constraint that chose this widening over a tidier one.
#   * **Every existing negative case still fails, unedited.** `0812345678` (wrong
#     prefix), `08100000` and `081000000` (too short), `0810000001x` (trailing junk)
#     and `""` are all still rejected, and the parametrised test guarding them in
#     `tests/test_testsets.py` needed no change to stay green. That was a requirement,
#     not a happy result: a widening that forces an edit to the test guarding it cannot
#     be reviewed, because the edit and the thing under review are the same act.
#   * **The control's rationale survives.** Still one contiguous, enumerable, obviously
#     synthetic block under the same `0810000` prefix. What this control has always
#     rested on is that prefix, not the last two digits, so lengthening the tail asserts
#     nothing new about the numbering plan that the old block did not already assert.
#
# The block is wider; it is still a block. `0810001000` is outside it and stays outside
# it, which is what the new negative cases in `tests/test_testsets.py` pin down.
CALL_ID_PATTERN = re.compile(r"^5[0-9]{3}$")
PHONE_PATTERN = re.compile(r"^0810000[0-9]{3}$")

# The scored columns, exactly (main.py:970-974, :1016-1020, :1033-1043).
GT_COLUMNS = frozenset({"product", "call_result", "main", "secondary", "third"})

# A citation is a file and a line, e.g. `prompt.txt:4` or `prompt.py:4390-4392`.
# Multiple citations may be separated by `;` or `,`.
_CITATION = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|txt|ya?ml|md):\d+(?:-\d+)?$")

# app -> label space. The reason spaces differ by exactly one class, which is data
# rather than a code fork (see labelspaces.py).
_LABEL_SPACES: dict[str, LabelSpace] = {"retention": RETENTION, "mnp": MNP}


def label_space_for(app: str) -> LabelSpace:
    """The label space one app is scored against, or a refusal.

    Public because a consumer that hard-codes `RETENTION` scores MNP's legal 12th reason
    class as an out-of-vocabulary answer, and silently: nothing downstream compares the
    space it used against the pack it read. Refusing on an unknown app is the point --
    a diagnostic that guesses a vocabulary reports a decoding failure that never happened.
    """
    space = _LABEL_SPACES.get(str(app).strip().lower())
    if space is None:
        raise TestsetError(
            f"unknown app {app!r}: no label space to score against. Known apps: "
            f"{', '.join(sorted(_LABEL_SPACES))}."
        )
    return space


@dataclass(frozen=True)
class TestItem:
    """One transcript and every claim made about it.

    Frozen to stop a consumer rebinding a field. The freeze is shallow — `gt`,
    `evidence` and `rules` hold plain dicts and stay mutable — so treat them as
    read-only by convention. Deep-freezing them would cost the JSON round-trip and
    equality behaviour that make these items easy to diff, which is the wrong trade for
    a fixture format whose whole job is being reviewable.
    """

    __test__ = False  # not a pytest class; see TestsetError

    item_id: str
    call_id: str
    family: str
    transcript_th: str
    phone_number: str | None
    mechanism: str
    why_it_matters: str
    gt: tuple[dict[str, str | None], ...]
    evidence: dict[str, str]
    rules: dict[str, str]
    expected_failure: str


@dataclass(frozen=True)
class TestSet:
    """A named, hashable collection of items for exactly one app."""

    __test__ = False  # not a pytest class; see TestsetError

    name: str
    app: str
    path: Path
    items: tuple[TestItem, ...]


_ITEM_FIELDS = tuple(f.name for f in fields(TestItem))
_REQUIRED_STR_FIELDS = (
    "item_id",
    "call_id",
    "family",
    "transcript_th",
    "mechanism",
    "why_it_matters",
    "expected_failure",
)


def testset_sha(path: str | Path) -> str:
    """sha256 of the file's bytes, hex.

    Bytes, not parsed content: the manifest records which exact file produced a score,
    and a re-encoding that leaves the JSON equivalent still changes what was scored.
    """
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


testset_sha.__test__ = False  # not a pytest function; see TestsetError


def load_testset(path: str | Path, *, app: str = "retention") -> TestSet:
    """Read a JSONL testset. UTF-8, no BOM, LF.

    Those three are enforced, not assumed:

      * **A BOM is rejected explicitly.** `utf-8-sig` would swallow it and every tool
        downstream would disagree about whether the first key is `item_id` or
        `\\ufeffitem_id`. Silently stripping it hides that a writer is misconfigured.
      * **A CR anywhere is rejected.** A stray `\\r` inside `transcript_th` changes the
        transcript's bytes without changing how it looks, so an evidence span can stop
        matching for a reason no reviewer can see on screen.
    """
    p = Path(path)
    raw = p.read_bytes()

    if raw.startswith(codecs.BOM_UTF8):
        raise TestsetError(
            f"{p} starts with a UTF-8 BOM. Testsets are UTF-8 with NO BOM: reading this "
            "with plain utf-8 puts \\ufeff inside the first key name, and reading it with "
            "utf-8-sig hides that the writer is misconfigured. Re-save without the BOM."
        )
    for bom, name in ((codecs.BOM_UTF16_LE, "UTF-16 LE"), (codecs.BOM_UTF16_BE, "UTF-16 BE")):
        if raw.startswith(bom):
            raise TestsetError(f"{p} starts with a {name} BOM. Testsets are UTF-8 with no BOM.")

    if b"\r" in raw:
        raise TestsetError(
            f"{p} contains a carriage return. Testsets are LF-only: a CR inside "
            "transcript_th changes the transcript's bytes invisibly, and an evidence span "
            "that no longer matches would look identical on screen to one that does."
        )

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:  # pragma: no cover - exercised via a crafted file
        raise TestsetError(
            f"{p} is not valid UTF-8 at byte {exc.start}: {exc.reason}. Thai text in "
            "another encoding (TIS-620, CP874) must be converted, not reinterpreted."
        ) from exc

    items: list[TestItem] = []
    for lineno, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        items.append(_item_from_line(p, lineno, line))

    return TestSet(name=p.stem, app=app, path=p, items=tuple(items))


def _item_from_line(path: Path, lineno: int, line: str) -> TestItem:
    """Parse one JSONL line into a TestItem, refusing anything structurally wrong."""
    where = f"{path}:{lineno}"
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        raise TestsetError(f"{where}: not valid JSON ({exc.msg} at column {exc.colno}).") from exc

    if not isinstance(record, dict):
        raise TestsetError(f"{where}: expected a JSON object, got {type(record).__name__}.")

    missing = [name for name in _ITEM_FIELDS if name not in record]
    if missing:
        raise TestsetError(f"{where}: missing required field(s): {', '.join(sorted(missing))}.")

    # Unknown keys are refused rather than ignored. A misspelled `evidences` would
    # otherwise drop every evidence span on the floor and the item would validate
    # clean, having proved nothing at all.
    unknown = [name for name in record if name not in _ITEM_FIELDS]
    if unknown:
        raise TestsetError(
            f"{where}: unknown field(s): {', '.join(sorted(unknown))}. A misspelled field "
            "name is silently dropped data, so it is an error here rather than a warning."
        )

    for name in _REQUIRED_STR_FIELDS:
        if not isinstance(record[name], str):
            raise TestsetError(
                f"{where}: {name} must be a string, got {type(record[name]).__name__}."
            )

    phone = record["phone_number"]
    if phone is not None and not isinstance(phone, str):
        raise TestsetError(
            f"{where}: phone_number must be a string or null, got {type(phone).__name__}. "
            "A number literal would drop the leading zero."
        )

    gt = record["gt"]
    if not isinstance(gt, list) or not all(isinstance(row, dict) for row in gt):
        raise TestsetError(f"{where}: gt must be a list of objects, one per product in the call.")

    for name in ("evidence", "rules"):
        block = record[name]
        if not isinstance(block, dict):
            raise TestsetError(f"{where}: {name} must be an object.")
        bad = sorted(k for k, v in block.items() if not isinstance(v, str))
        if bad:
            raise TestsetError(f"{where}: {name} values must be strings; not so for {bad}.")

    return TestItem(
        item_id=record["item_id"],
        call_id=record["call_id"],
        family=record["family"],
        transcript_th=record["transcript_th"],
        phone_number=phone,
        mechanism=record["mechanism"],
        why_it_matters=record["why_it_matters"],
        gt=tuple(dict(row) for row in gt),
        evidence=dict(record["evidence"]),
        rules=dict(record["rules"]),
        expected_failure=record["expected_failure"],
    )


def _cell(value: object) -> str | None:
    """Normalise one gt cell: blanks and nulls are 'no label', not a label named ''."""
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _labels_in_row(row: dict) -> list[tuple[str, str]]:
    """Every label a gt row asserts, as (dimension, value).

    Reasons are comma-split exactly as `get_reasons_set` splits them
    (`fact_checker.py:877`), so a multi-label cell owes evidence and a rule per label
    rather than one apiece for the cell.
    """
    labels: list[tuple[str, str]] = []
    for dimension in ("product", "call_result"):
        value = _cell(row.get(dimension))
        if value is not None:
            labels.append((dimension, value))
    for column in REASON_COLUMNS:
        raw = row.get(column)
        if raw is None:
            continue
        for part in str(raw).split(","):
            value = part.strip().lower()
            if value:
                labels.append(("reason", value))
    return labels


def _lower_keys(block: dict[str, str]) -> dict[str, str]:
    """Index a block case-insensitively.

    Production folds case on every string column before scoring
    (`fact_checker.py:732-739`), so `ev_product:Postpaid` and `ev_product:postpaid`
    name the same label and neither spelling should be a validation failure.
    """
    return {key.strip().lower(): value for key, value in block.items()}


def validate(ts: TestSet) -> list[str]:
    """Return every problem found, each naming the item it belongs to. Empty means OK.

    Returns rather than raises: a testset is reviewed as a whole, and a validator that
    stops at the first problem turns one review into ten.

    What is deliberately NOT checked: whether `mechanism`, `why_it_matters` and
    `expected_failure` say anything worth reading. Those are review questions, and a
    non-emptiness assertion on them would buy the appearance of rigour without the
    substance. `expected_failure` in particular is only meaningful if a real model
    plausibly fails that way, which no regex can know.
    """
    problems: list[str] = []

    space = _LABEL_SPACES.get(ts.app.strip().lower())
    if space is None:
        problems.append(
            f"{ts.name}: unknown app {ts.app!r}, so no label space could be applied. "
            f"Known apps: {', '.join(sorted(_LABEL_SPACES))}. Label membership was NOT "
            "checked for any item in this file."
        )

    seen: dict[str, int] = {}
    for position, item in enumerate(ts.items, start=1):
        if item.item_id in seen:
            problems.append(
                f"{item.item_id}: duplicate item_id (also at item {seen[item.item_id]}, "
                f"again at item {position}). Item ids key every per-item result, so a "
                "duplicate silently overwrites a scored row."
            )
        else:
            seen[item.item_id] = position

    for item in ts.items:
        problems.extend(_validate_item(item, space))

    return problems


def _validate_item(item: TestItem, space: LabelSpace | None) -> list[str]:
    problems: list[str] = []
    ident = item.item_id or "<missing item_id>"

    if not item.item_id.strip():
        problems.append(
            "<missing item_id>: item_id is empty, so no problem found in this item can "
            "be reported against anything."
        )

    if not CALL_ID_PATTERN.fullmatch(item.call_id):
        problems.append(
            f"{ident}: call_id {item.call_id!r} is outside the synthetic range. It must "
            "match ^5[0-9]{3}$ (5000-5999). Any other value risks being, or being "
            "mistaken for, a real True call id, and no real customer data enters this repo."
        )

    if item.phone_number is not None and not PHONE_PATTERN.fullmatch(item.phone_number):
        problems.append(
            f"{ident}: phone_number {item.phone_number!r} is outside the synthetic block. "
            # Quoted from PHONE_PATTERN rather than retyped. The block was widened once
            # already (2026-08-06) and a hardcoded copy here would have gone stale in
            # the one place a reviewer reads it: the message telling them what to fix.
            f"It must match {PHONE_PATTERN.pattern} or be null. A null phone is legal and "
            "has a consequence worth intending: production groups on "
            "['call_id','phone_number'] without dropna=False (fact_checker.py:1080), so "
            "the row never reaches the product metric."
        )

    if not item.transcript_th.strip():
        problems.append(
            f"{ident}: transcript_th is empty. Every evidence span is checked against it, "
            "so an empty transcript makes every label unfalsifiable."
        )

    problems.extend(_validate_evidence_spans(item, ident))
    problems.extend(_validate_gt_rows(item, ident, space))
    problems.extend(_validate_citations(item, ident))
    return problems


def _validate_evidence_spans(item: TestItem, ident: str) -> list[str]:
    """THE check. Every evidence span must appear verbatim in this item's transcript.

    Applies to every entry in `evidence`, including dimensions that are not scored:
    a documented span that is not in the transcript is wrong in exactly the same way.
    """
    problems: list[str] = []
    for key in sorted(item.evidence):
        span = item.evidence[key]
        if not span.strip():
            # `"" in text` is True, so an empty span would pass the substring test while
            # asserting nothing. It has to be caught before that test, not by it.
            problems.append(
                f"{ident}: evidence {key!r} is empty. An empty span is a substring of "
                "every transcript, so it would pass the verbatim check while proving "
                "nothing about this label."
            )
            continue
        if span not in item.transcript_th:
            problems.append(
                f"{ident}: evidence {key!r} is not a verbatim substring of transcript_th: "
                f"{span!r}. Either the span was edited away from the transcript or the "
                "transcript was edited away from the span. Fix the transcript so the span "
                "appears verbatim, or drop the label; do not reword the span to fit."
            )
    return problems


def _validate_gt_rows(item: TestItem, ident: str, space: LabelSpace | None) -> list[str]:
    problems: list[str] = []

    if not item.gt:
        problems.append(
            f"{ident}: gt is empty, so the item asserts nothing. The grain is one row per "
            "product mentioned in the call (fact_checker.py:1075)."
        )

    evidence = _lower_keys(item.evidence)
    rules = _lower_keys(item.rules)
    products_seen: dict[str, int] = {}

    for index, row in enumerate(item.gt, start=1):
        keys = set(row)
        if keys != GT_COLUMNS:
            missing = sorted(GT_COLUMNS - keys)
            extra = sorted(keys - GT_COLUMNS)
            detail = []
            if missing:
                detail.append(f"missing {missing}")
            if extra:
                detail.append(f"unexpected {extra}")
            problems.append(
                f"{ident}: gt row {index} keys must be exactly "
                f"{sorted(GT_COLUMNS)} — {', and '.join(detail)}. Write every column, "
                "using null for 'no label', so an absent cell is never confused with an "
                "author who forgot the column exists."
            )

        product = _cell(row.get("product"))
        if product is not None:
            if product in products_seen:
                problems.append(
                    f"{ident}: gt rows {products_seen[product]} and {index} both claim "
                    f"product {product!r}. The scored row is "
                    "(call_id, phone_number, product) (fact_checker.py:1075), so two rows "
                    "for one product are two conflicting answers to the same key."
                )
            else:
                products_seen[product] = index

        for dimension, value in _labels_in_row(row):
            token = f"{dimension}:{value}"

            if f"rule_{token}" not in rules:
                problems.append(
                    f"{ident}: gt row {index} asserts {token!r} with no rules entry. Add "
                    f"'rule_{token}' citing the production file:line that makes the "
                    "evidence span decisive. A label no production line supports is not a "
                    "ground truth, it is an opinion."
                )
            if f"ev_{token}" not in evidence:
                problems.append(
                    f"{ident}: gt row {index} asserts {token!r} with no evidence entry. Add "
                    f"'ev_{token}' quoting the verbatim span of this transcript that carries "
                    "the label."
                )

            if space is None:
                continue
            allowed = {
                "product": space.product,
                "call_result": space.call_result,
                "reason": space.reason,
            }[dimension]
            if value not in {candidate.lower() for candidate in allowed}:
                problems.append(
                    f"{ident}: gt row {index} uses {value!r} for {dimension}, which is not "
                    f"in the {space.app} label space. Permitted: "
                    f"{', '.join(sorted(allowed))}. The vocabulary is transcribed verbatim "
                    "from production and is closed — a near miss (undefine for undefined, "
                    "Prepaid for a product) scores as a wrong label, not as a typo."
                )

    return problems


def _validate_citations(item: TestItem, ident: str) -> list[str]:
    """Rules must look like `file.py:123` or `file.txt:12-20`, `;`- or `,`-separated.

    Form only. Whether the cited line says what the item claims is a reading question,
    and this function does not pretend to have read it.
    """
    problems: list[str] = []
    for key in sorted(item.rules):
        value = item.rules[key]
        citations = [part.strip() for part in re.split(r"[;,]", value) if part.strip()]
        if not citations:
            problems.append(f"{ident}: rules {key!r} is empty; it must cite a file and line.")
            continue
        malformed = [c for c in citations if not _CITATION.fullmatch(c)]
        if malformed:
            problems.append(
                f"{ident}: rules {key!r} has malformed citation(s) {malformed}. Use "
                "file:line, e.g. 'prompt.txt:4' or 'prompt.py:4390-4392', separated by "
                "';'. A citation without a line number cannot be checked by a reviewer, "
                "which is the only thing that makes it worth writing down."
            )
    return problems
