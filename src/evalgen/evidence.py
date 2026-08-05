"""Whether a model's `keyword` spans are actually in the transcript, at two grains.

What this measures
------------------
Each reason slot carries a `keyword` field the model is told to fill with "keywords or
short phrases directly from the audio" (`schemas/retention.json:35`, production
`main.py:977`). This module asks one question of each of those strings: **is it there,
byte for byte, in `transcript_th`?** It asks twice, at two grains, and reports both
side by side, because the two answers disagree and the disagreement is the finding.

  * **whole-field** -- is the entire `keyword` string a substring of the transcript?
  * **comma-split** -- split the field on `,`, strip each part, ask of each segment?

Nothing here is scored, ranked or compared. `evidence_rates` takes ONE run directory
and returns counts for it. There is no verdict, no pass/fail, and no second arm to be
better than: see "This is not a fourth scored dimension" below, which is a constraint
on the module's shape and not only on its prose.

READ THIS BEFORE QUOTING `whole_field_rate`: it is a FORMAT measurement
-----------------------------------------------------------------------
The gap between `whole_field_rate` and `segment_rate` is a difference in **output
format**, not in **fidelity to the transcript**. Both `schemas/retention.json:35` and
production `main.py:977` end the field description with the literal instruction **"Use
comma separation."** A model that complies emits one field holding several segments;
that field is not a substring of the transcript, because the transcript never contains
the model's comma. A model that ignores the instruction and emits one segment per field
scores a perfect whole-field rate for doing less.

MEASURED on the two committed base runs, per replicate (not assumed -- the derivation
is in the hand computation this module was built against, and `tests/test_evidence.py`
pins every number below as a literal):

    out/runs/20260805-005641Z-incumbent-base   google/gemini-2.5-flash
        spans_whole 40   verbatim_whole 27   whole_field_rate 0.675
        spans_split 63   verbatim_split 63   segment_rate     1.000
        comma_fields 13

    out/runs/20260805-005643Z-candidate-base   qwen/qwen3.6-27b
        spans_whole 44   verbatim_whole 42   whole_field_rate 0.955
        spans_split 44   verbatim_split 42   segment_rate     0.955
        comma_fields  0

Read the whole-field row alone and Qwen leads, 0.955 to 0.675. That ranking is an
artifact of format compliance and reporting it as fidelity would be wrong. **All 13 of
Gemini's whole-field failures are comma-joins and nothing else** -- the set of
comma-bearing fields and the set of whole-field failures are the same 13 fields, with
zero exceptions in either direction (measured; `tests/test_evidence.py` pins the set
equality, not just the two counts). After splitting on the separator the schema asked
for, every one of Gemini's 63 segments is byte-exact. Gemini's whole-field number is low
*because it complied*. Qwen emitted no commas at all, so `spans_split == spans_whole`
for that arm and its two rates are the same measurement written twice.

The only figure comparable across the two arms is `segment_rate`.

Denominators are not comparable across arms either, and `spans_whole` is not a measure
of evidence volume: Qwen always emits all three slots and leaves 22 of its 66 keyword
fields blank; Gemini omits slots it has nothing for and leaves 6 of 46 blank. 40 versus
44 is emission policy.

`keyword` is not scored anywhere
--------------------------------
Production's scorer reads `keyword` **zero** times -- the reason label is scored,
the evidence for it is not (`src/evalgen/decoding.py:130`: "`keyword` is not scored";
`src/evalgen/flatten.py:27`: the slot object is `{"reason": ..., "keyword": ...}` and
"only `.reason` is scored"; `src/evalharness/` contains no occurrence of the string
`keyword` at all). Nothing in this module can move a scored number, and a change in
these counts is not a regression in anything the harness grades.

This is not a fourth scored dimension
-------------------------------------
The three scored dimensions live in `evalharness`, are computed against a hand-computed
fixture, and produce a rate that decides things. This module produces neither a headline
nor a verdict, deliberately:

  * it returns **two** rates and no blended one, because a single number here would have
    to pick a grain and the whole point is that the grain changes the answer;
  * `format_gap` is returned already named as a format quantity, so it cannot be quoted
    as a fidelity delta without contradicting its own key;
  * the signature takes one run directory. There is no `compare(a, b)`, and adding one
    would need its own hand-computed expectation first.

Near-misses are counted apart from misses, and are never netted against them
---------------------------------------------------------------------------
A segment that is not a byte-exact substring gets asked a second question before it is
counted as missing: does it match after ASR orthography is normalised on **both** sides?

    s.replace("เเ", "แ").replace("​", "")   # เเ -> แ ; drop ZWSP

Both artifacts are the ones the pack documents for the `thai_linguistic` family
(`tests/fixtures/testsets/README.md:61`: "ASR orthography (`เเ` for `แ`, one U+200B)").
MEASURED in the transcripts: **RET-11 alone** carries them, 12 occurrences of `เเ`
(U+0E40 U+0E40) and 1 U+200B. Every other item: zero of both.

`near_miss` and `hard_miss` are separate keys with separate segment lists and are never
summed into a "violations" figure. A near-miss is a model that transcribed Thai
correctly against a transcript that did not; a hard miss is text that is not in the
transcript in any orthography. Netting them would let a model be credited for a defect,
or -- much worse here -- blamed for a correction.

**MEASURED: `near_miss` is 0 on both arms, because neither model normalised.** Both
reproduced the artifact byte-for-byte and therefore matched exactly, never reaching the
near-miss branch. Gemini's `RET-11 Postpaid.secondary` keeps both of its `เเ`; Qwen's
`RET-11 Postpaid.third` keeps its one. No keyword field in either arm contains a single
U+0E41 or U+200B. That is a fact about these two models, not about this detector: three
hand-normalised RET-11 spans -- what a correctly-normalising model would have emitted --
all fail the exact test and all fire the near-miss branch, and
`tests/test_evidence.py` runs exactly those three as a non-vacuity control. The branch
ships because the moment a model does the right thing, a detector without it would score
that model as having made the text up.

`agent_only` is 0 on both arms, and that detector is not vacuous either
----------------------------------------------------------------------
`prompt.py:4382-4387` requires the reason phrase to be customer speech, so a span
quoted only from an agent turn is a real defect. Turn structure was MEASURED, not
assumed: transcripts are `\n`-separated and every one of the 366 lines across the 20
items carries exactly one of two prefixes -- `เจ้าหน้าที่:` (agent, 184 lines) and
`ลูกค้า:` (customer, 182 lines). **Zero unprefixed lines**, so the split is total and
there is no residue bucket hiding a miscount.

All 63 Gemini and all 42 Qwen matching segments sit inside a customer turn; none sits
only in an agent turn. Control, for the same reason as above: the first 17 characters of
the opening agent turn of RET-01, RET-11 and RET-17 are each absent from every customer
turn and present in an agent turn, so all three classify as `agent_only`.

The 2 Qwen segments that fail are turn-boundary joins, NOT fabrications
----------------------------------------------------------------------
This is where a reader will draw the wrong conclusion, so it is stated here rather than
left to whoever reads the counts. Both hard misses are `RET-15 Postpaid`, and every
character of both is transcript-derived. `main` is customer turn 3 followed by customer
turn 4; `secondary` is customer turn 6 followed by a prefix of customer turn 7. In both
cases the newline between the turns was replaced by the single space that already sits
after `ลูกค้า:`, and the reconstruction from the two turn bodies is character-exact.
Gemini quoted the *same four* customer turns on RET-15, as four comma-separated
segments, and matched 4/4.

Under this module's definition -- byte-exact substring of `transcript_th` -- those two
correctly count as `hard_miss`, and correctly are not `near_miss`: the near-miss rule
covers ASR orthography and nothing else. **Do not label them fabrication.** The failure
is span assembly across a turn boundary. If a `join` category is ever wanted it arrives
as a new category with its own hand-computed expectation, never by widening
`asr_normalise` until the number goes away.

Choices this measurement had to make, and what each one cost
------------------------------------------------------------
1. **A blank keyword is absent, not a zero-length span.** There are no JSON nulls under
   `payload.product` in either run; both arms write `""` for "no keyword" (Gemini 6,
   Qwen 22). Taken literally, "non-null keyword" would select all of them, and `""` is a
   substring of every transcript, so every blank would score one free "verbatim". This
   module follows the repo's own normalisation instead -- `records.py:29`
   (`return text or None`) and `records.py:59` (`if cleaned:`) -- and treats
   blank-after-strip as absent. MEASURED cost of the alternative: Gemini `spans_whole`
   46 / `verbatim_whole` 33, Qwen 66 / 64, which is `whole_field_rate` 0.97 for a model
   that filled two thirds of its slots with nothing. Every other counter is unaffected;
   a blank field contributes no segment and holds no comma.

2. **Split and strip, do NOT lowercase.** `records.py:58` lowercases when it splits
   reason labels; this module does not, because it is defined as byte-exact and the
   transcripts contain uppercase ASCII (`PIN`, in RET-11). Case-folding only the model
   side would manufacture failures. MEASURED impact on these two runs: **0 segments**
   either way -- no segment in either arm contains an ASCII uppercase letter. It costs
   nothing today and would bite the first time a model quotes `PIN`, which is why the
   choice is written down here rather than left implicit in the absence of a `.lower()`.

3. **Occurrences, not distinct texts.** Moot on this data -- distinct `(item, segment)`
   pairs equal distinct texts equal the totals, 63 and 44, because no segment repeats --
   but stated because a duplicate will eventually appear and the two readings will part.

4. **A verbatim segment that matches no single turn is its own bucket.** It is neither
   `customer_speech` nor `agent_only`, and is counted as `cross_turn` rather than
   assigned to either. MEASURED 0 in both arms, and structurally 0 here since no keyword
   field contains a newline. The bucket exists so that
   `customer_speech + agent_only + cross_turn == verbatim_split` is a true invariant;
   without it `customer_speech + agent_only == verbatim_split` would be a silently false
   one, and the day it broke it would break as a wrong number rather than an error.

5. **A turn body is everything after `prefix:`, leading space kept.** Stripping it
   cannot change the placement counts: segments are already stripped, so a stripped
   segment is a substring of `" body"` exactly when it is a substring of `"body"`.

6. **U+2014 and U+201C/U+201D are deliberately NOT in `asr_normalise`.** RET-16 and
   RET-20 contain them, but the pack classifies them as self-repair/truncation markers
   and the escape family's nested quotation marks -- not ASR orthography. Folding them
   in would be widening the near-miss rule to catch misses, which is the failure mode
   the near-miss/hard-miss split exists to prevent. MEASURED impact: 0 segments in
   either arm contain any of the three.

Provenance is checked, not trusted -- and the one place it is allowed to be waived
----------------------------------------------------------------------------------
Matching model output against a transcript the model was never shown produces a number
that looks exactly like a real one. So `run.json`'s `testset_sha` is compared against
the sha of the file passed in -- the same guard `cli.py:1159` applies before scoring --
and a mismatch raises by default. Both committed base runs record
`6e761141c4096a066585b2643ca5510a67bce6b9baa9f7159d6e3edb6b52d79b`.

`require_recorded_sha=False` waives that raise, and it exists for one specific,
demonstrated reason rather than for convenience. **A testset file has two sides, and
this module reads only one of them.** The counts here depend on `transcript_th` and on
nothing else; `gt`, `evidence` and `rules` are the label side, they are edited routinely
as the pack is refined, and no edit to them can move a single number in this module. The
whole-file sha cannot tell the two apart, so a label-side edit makes it report a
transcript-side hazard that did not happen.

That is not hypothetical. MEASURED on 2026-08-05: `retention_v1.jsonl` was edited to
give RET-11 a second reason label, which changed exactly three fields -- `gt`,
`evidence` and `rules`, all on RET-11 -- and moved the file sha from
`6e761141...` to `c367a478...`. All 20 transcripts stayed byte-identical, and all eight
counts in both arms stayed identical across all three replicates. The recorded sha still
matches the file at `HEAD`; it is the working-tree copy that moved.

Waiving is therefore narrow and it is never silent: the flag is not the default, and the
result carries `testset_sha_verified: False` together with `testset_sha_actual`, so the
mismatch travels with the numbers instead of being dropped where they were computed.
What it gives up is real and is stated here rather than implied -- with the sha waived,
nothing in this module can prove the transcripts are the ones the model saw. That
obligation moves to the caller, and `tests/test_evidence.py` discharges it by pinning a
digest of the transcripts alone (`9fa94ea9b51cf4bb...`), so a transcript-side edit fails
loudly and invalidates the pinned counts, while a label-side edit correctly does not.

Replicates are summed, and also reported apart
----------------------------------------------
The totals cover every record in `run.jsonl`, all three replicates, matching
`fabrication.fabrication_rate`. Three replicates of one item are not three independent
observations, so `by_replicate` carries each one separately. MEASURED: all eight counts
are identical across replicates 1, 2 and 3 in both arms -- all six run x replicate cells
agree -- so the numbers above are not a replicate-1 accident, and the totals are exactly
three times them.

Agreement with the hand computation
-----------------------------------
The expectation for this module was hand-derived from the run logs and the testset
before any of this code existed. This implementation reproduces it exactly: all eight
counts in both arms, all three replicates, both non-vacuity controls, the set equality
between Gemini's comma fields and its whole-field failures, the RET-15 join
reconstruction, and every alternative number quoted under the choices above.
**No disagreement was found**, so nothing needed adjudicating and no expectation was
edited.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evalgen.testsets import load_testset, testset_sha
from evalharness.labelspaces import REASON_COLUMNS

__all__ = [
    "AGENT_PREFIX",
    "ASR_SUBSTITUTIONS",
    "CUSTOMER_PREFIX",
    "EvidenceError",
    "asr_normalise",
    "evidence_rates",
]

# The two speaker prefixes, MEASURED rather than assumed: across the 20 items every one
# of the 366 transcript lines starts with exactly one of these, and none starts with
# neither. See the module docstring.
AGENT_PREFIX = "เจ้าหน้าที่:"
CUSTOMER_PREFIX = "ลูกค้า:"

# ASR orthography only. `เเ` (U+0E40 twice) is the artifact the pack injects for `แ`
# (U+0E41), and U+200B is the one zero-width space. Both are documented for the
# `thai_linguistic` family in `tests/fixtures/testsets/README.md:61`, and both occur in
# RET-11 and nowhere else.
#
# Nothing else belongs in here. This table is what separates "the model corrected the
# transcript" from "the model made it up", and every entry added to it moves a hard miss
# into the near-miss bucket. Widening it to make a number look better is the exact
# failure the split was built to prevent -- a new class of forgiveness is a new category
# with its own hand-computed expectation, not a new row here.
ASR_SUBSTITUTIONS: tuple[tuple[str, str], ...] = (
    ("เเ", "แ"),  # เเ -> แ
    ("​", ""),  # zero-width space
)

# The counters accumulated per replicate and then summed. Kept as a tuple so a new
# counter cannot be added to one code path and forgotten in the other.
_COUNT_KEYS: tuple[str, ...] = (
    "records",
    "product_blocks",
    "keyword_slots_present",
    "blank_keyword_slots",
    "nonstring_keyword_slots",
    "spans_whole",
    "verbatim_whole",
    "comma_fields",
    "spans_split",
    "verbatim_split",
    "customer_speech",
    "agent_only",
    "cross_turn",
    "near_miss",
    "hard_miss",
)


class EvidenceError(ValueError):
    """Raised when the inputs cannot support the count being asked for.

    Loud rather than best-effort, for the same reason `FabricationError` is: every
    condition this raises on still produces a number if it is ignored, and once that
    number reaches a report it is indistinguishable from one computed off a sound join.

    The condition worth naming is the testset sha mismatch. A verbatim rate computed
    against a transcript the model was never shown is not a low score -- it is a
    meaningless one that reads as a low score, and it would be quoted as evidence the
    model invents its evidence.
    """


def asr_normalise(text: str) -> str:
    """Apply `ASR_SUBSTITUTIONS`, in order.

    Applied to BOTH the segment and the transcript before a near-miss comparison. A
    one-sided normalisation would answer a different question -- "did the model write
    what the transcript writes" rather than "do these two agree once the known ASR
    artifact is removed from each" -- and would report a correcting model and a
    corrupting model identically.
    """
    for old, new in ASR_SUBSTITUTIONS:
        text = text.replace(old, new)
    return text


def evidence_rates(
    run_dir: str | Path,
    testset_path: str | Path,
    *,
    require_recorded_sha: bool = True,
) -> dict:
    """Count how much of a run's `keyword` text is really in the transcript.

    Returns a dict holding, for the run as a whole and again per replicate under
    `by_replicate`:

      * `spans_whole` / `verbatim_whole` / `whole_field_rate` -- the whole-field grain
      * `spans_split` / `verbatim_split` / `segment_rate` -- the comma-split grain
      * `comma_fields` and `format_gap` -- the size of, and the reason for, the gap
        between those two rates. **A format difference, not a fidelity difference.**
      * `customer_speech` / `agent_only` / `cross_turn` -- where each matching segment
        sits. Sums to `verbatim_split`.
      * `near_miss` / `hard_miss` -- separate counters, never netted. Together with
        `verbatim_split` they sum to `spans_split`.

    plus the segment listings (`near_miss_segments`, `hard_miss_segments`,
    `agent_only_segments`, `comma_field_slots`) so no count has to be taken on trust,
    and the provenance and denominator diagnostics the rates must not be read without.

    Neither rate is "the" rate and there is no combined one. `segment_rate` is the only
    figure comparable across models; see the module docstring before quoting the other.

    `require_recorded_sha=False` waives the refusal to run when `run.json`'s
    `testset_sha` does not match the file, for the single case the module docstring sets
    out: a label-side edit to the testset, which cannot move any count here. It is never
    silent -- the result reports `testset_sha_verified: False` and `testset_sha_actual`
    -- and it shifts to the caller the job of showing the transcripts have not moved.
    """
    run_dir = Path(run_dir)
    testset_path = Path(testset_path)

    transcripts = _load_transcripts(testset_path)
    meta = _read_meta(run_dir)
    verified, actual_sha = _check_provenance(
        meta, testset_path, require_recorded_sha=require_recorded_sha
    )

    per_replicate: dict[Any, dict] = {}
    near_miss_segments: list[dict] = []
    hard_miss_segments: list[dict] = []
    agent_only_segments: list[dict] = []
    comma_field_slots: list[dict] = []
    skipped = 0
    records_read = 0

    for record in _read_records(run_dir):
        records_read += 1
        if not record.get("parse_ok"):
            # Mirrors `fabrication.fabrication_rate`: `outcomes.Classified.payload` is
            # populated even on a schema violation, and its docstring says that payload
            # is for reading rather than scoring. On both base runs this changes nothing
            # -- all 60 records parse -- but a variant run with a different violation
            # count would otherwise move these numbers for a reason that is not evidence.
            skipped += 1
            continue

        item_id = record.get("item_id")
        transcript = transcripts.get(item_id)
        if transcript is None:
            raise EvidenceError(
                f"{run_dir / 'run.jsonl'} has a record for item {item_id!r}, which is "
                f"not in {testset_path}. Every keyword span for that record would be "
                "unaskable and would silently vanish from the denominator, quietly "
                "raising the verbatim rate. Point at the testset the run actually used."
            )

        counts = per_replicate.setdefault(record.get("replicate"), _zero_counts())
        counts["records"] += 1
        agent_turns, customer_turns = _turns(transcript)
        normalised_transcript = asr_normalise(transcript)

        for product, block in _product_blocks(record.get("payload")):
            counts["product_blocks"] += 1
            for slot in REASON_COLUMNS:
                nested = block.get(slot)
                if not isinstance(nested, dict):
                    continue
                keyword = nested.get("keyword")
                if not isinstance(keyword, str):
                    # Absent, null, or a list the model emitted instead of a string.
                    # Mirrors `flatten._reason`: stringifying it would manufacture spans
                    # the model never claimed and count them all as fabricated.
                    if keyword is not None:
                        counts["nonstring_keyword_slots"] += 1
                    continue

                counts["keyword_slots_present"] += 1
                if not keyword.strip():
                    # Blank is absent, not a zero-length span. See choice 1 in the
                    # module docstring: `"" in transcript` is True for every transcript.
                    counts["blank_keyword_slots"] += 1
                    continue

                where = {"item_id": item_id, "product": product, "slot": slot}

                counts["spans_whole"] += 1
                if keyword in transcript:
                    counts["verbatim_whole"] += 1
                if "," in keyword:
                    counts["comma_fields"] += 1
                    comma_field_slots.append({**where, "keyword": keyword})

                for segment in _segments(keyword):
                    counts["spans_split"] += 1
                    if segment in transcript:
                        counts["verbatim_split"] += 1
                        in_customer = any(segment in turn for turn in customer_turns)
                        in_agent = any(segment in turn for turn in agent_turns)
                        if in_customer:
                            counts["customer_speech"] += 1
                        elif in_agent:
                            counts["agent_only"] += 1
                            agent_only_segments.append({**where, "segment": segment})
                        else:
                            counts["cross_turn"] += 1
                    elif asr_normalise(segment) in normalised_transcript:
                        counts["near_miss"] += 1
                        near_miss_segments.append({**where, "segment": segment})
                    else:
                        counts["hard_miss"] += 1
                        hard_miss_segments.append({**where, "segment": segment})

    totals = _zero_counts()
    for counts in per_replicate.values():
        for key in _COUNT_KEYS:
            totals[key] += counts[key]

    _check_invariants(totals)

    return {
        "run_dir": str(run_dir),
        "run_id": meta.get("run_id"),
        "arm": meta.get("arm"),
        "model_requested": meta.get("model_requested"),
        "prompt_id": meta.get("prompt_id"),
        "testset_path": str(testset_path),
        "testset_sha": meta.get("testset_sha"),
        "testset_sha_actual": actual_sha,
        "testset_sha_verified": verified,
        **_with_rates(totals),
        "by_replicate": {
            replicate: _with_rates(counts)
            for replicate, counts in sorted(
                per_replicate.items(), key=lambda kv: (kv[0] is None, kv[0])
            )
        },
        # Listings, so no count has to be taken on trust. Kept as four separate lists
        # for the same reason the counters are separate: a near miss and a hard miss are
        # different findings about different models and must not be concatenated.
        "near_miss_segments": near_miss_segments,
        "hard_miss_segments": hard_miss_segments,
        "agent_only_segments": agent_only_segments,
        "comma_field_slots": comma_field_slots,
        "records_read": records_read,
        "records_skipped_not_parse_ok": skipped,
    }


def _with_rates(counts: dict) -> dict:
    """Add the two rates and the format gap to a bare count block.

    Both rates are 0.0 rather than `nan` on an empty denominator: a run that emitted no
    spans has no rate, and `nan` prints as a defect in whatever renders it.

    `format_gap` is `segment_rate - whole_field_rate`, named for what it is. It is how
    much of the whole-field shortfall is explained by the model having obeyed "Use comma
    separation" (`schemas/retention.json:35`, `main.py:977`). It is NOT a fidelity delta
    and is deliberately not called one.
    """
    whole = (counts["verbatim_whole"] / counts["spans_whole"]) if counts["spans_whole"] else 0.0
    split = (counts["verbatim_split"] / counts["spans_split"]) if counts["spans_split"] else 0.0
    return {
        **{key: counts[key] for key in _COUNT_KEYS},
        "whole_field_rate": whole,
        "segment_rate": split,
        "format_gap": split - whole,
    }


def _check_invariants(counts: dict) -> None:
    """Refuse to return counts whose buckets do not partition their denominator.

    Both identities below hold by construction of the loop above. They are checked
    anyway, because the way this module gets quietly broken is somebody adding a fourth
    placement bucket or a second kind of forgiveness and wiring it into only one branch.
    That edit leaves every individual number plausible and makes the totals wrong; this
    turns it into an error at the point it happens.
    """
    placement = counts["customer_speech"] + counts["agent_only"] + counts["cross_turn"]
    if placement != counts["verbatim_split"]:
        raise EvidenceError(
            f"placement buckets sum to {placement} but {counts['verbatim_split']} "
            "segments matched. Every verbatim segment sits in a customer turn, an agent "
            "turn, or across a boundary; a fourth outcome means a bucket is missing."
        )
    outcomes = counts["verbatim_split"] + counts["near_miss"] + counts["hard_miss"]
    if outcomes != counts["spans_split"]:
        raise EvidenceError(
            f"segment outcomes sum to {outcomes} but {counts['spans_split']} segments "
            "were examined. verbatim, near_miss and hard_miss partition the segments and "
            "are never netted against one another."
        )
    slots = counts["spans_whole"] + counts["blank_keyword_slots"]
    if slots != counts["keyword_slots_present"]:
        raise EvidenceError(
            f"{slots} non-blank + blank keyword slots but {counts['keyword_slots_present']} "
            "present. A slot is blank or it is a span; there is no third state."
        )


def _zero_counts() -> dict:
    return {key: 0 for key in _COUNT_KEYS}


def _segments(keyword: str) -> list[str]:
    """`"a, b"` -> `["a", "b"]`. Split and strip, and deliberately NOT lowercase.

    `records.py:57-60` also lowercases, because it is normalising a label into a closed
    vocabulary. This is comparing free text against a transcript byte for byte, and the
    transcripts contain uppercase ASCII (`PIN` in RET-11), so folding only the model's
    side would manufacture failures. MEASURED impact on the two base runs: 0 segments
    either way, because no segment in either arm contains an ASCII uppercase letter.
    """
    return [part.strip() for part in keyword.split(",") if part.strip()]


def _turns(transcript: str) -> tuple[list[str], list[str]]:
    """Split a transcript into (agent bodies, customer bodies).

    Lines carrying neither prefix are dropped rather than guessed at. MEASURED: there
    are none across the 20 committed items -- 366 lines, 184 agent, 182 customer, 0
    unprefixed -- so on this data nothing is dropped. If a future transcript adds an
    unprefixed line, its text becomes unattributable and the segments quoting it fall
    into `cross_turn`, which is visible, rather than being credited to a speaker that
    was assumed.
    """
    agent: list[str] = []
    customer: list[str] = []
    for line in transcript.split("\n"):
        if line.startswith(AGENT_PREFIX):
            agent.append(line[len(AGENT_PREFIX) :])
        elif line.startswith(CUSTOMER_PREFIX):
            customer.append(line[len(CUSTOMER_PREFIX) :])
    return agent, customer


def _product_blocks(payload: Any) -> list[tuple[Any, dict]]:
    """`payload["product"]` entries that are objects. Mirrors `fabrication._product_blocks`.

    A null or non-object block carries no keyword slots, so it contributes no span.
    """
    if not isinstance(payload, dict):
        return []
    products = payload.get("product")
    if not isinstance(products, dict):
        return []
    return [(name, block) for name, block in products.items() if isinstance(block, dict)]


def _read_records(run_dir: Path) -> list[dict]:
    """Every JSON object in `<run_dir>/run.jsonl`, in file order."""
    path = run_dir / "run.jsonl"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise EvidenceError(
            f"no run log at {path}. `evidence_rates` reads the per-replicate records, "
            "not the run.json summary, because the summary carries no payloads."
        ) from exc
    records = []
    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise EvidenceError(f"{path}:{number} is not JSON: {exc}") from exc
    if not records:
        raise EvidenceError(f"{path} holds no records")
    return records


def _read_meta(run_dir: Path) -> dict:
    """`run.json` if it is there, `{}` if it is not. Provenance, never arithmetic."""
    path = run_dir / "run.json"
    if not path.exists():
        return {}
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return meta if isinstance(meta, dict) else {}


def _load_transcripts(testset_path: Path) -> dict[str, str]:
    """`item_id` -> `transcript_th`, read through the testset loader rather than by hand.

    `load_testset` is used instead of a local JSONL read specifically for its byte-level
    guards, which are load-bearing here in a way they are not everywhere: it refuses a
    BOM and refuses any CR. A stray `\\r` inside `transcript_th` changes the transcript's
    bytes without changing how it looks on screen, so a span could stop matching for a
    reason no reviewer could see -- and this module's entire output is byte-exact
    substring tests against exactly that text.

    Duplicate `item_id`s raise. The loader does not check for them, and a duplicate here
    would mean every span for that item is silently checked against whichever transcript
    happened to be last.
    """
    if not testset_path.exists():
        raise EvidenceError(f"no testset at {testset_path}")
    testset = load_testset(testset_path)
    transcripts: dict[str, str] = {}
    for item in testset.items:
        if item.item_id in transcripts:
            raise EvidenceError(
                f"{testset_path} has two items with item_id {item.item_id!r}. Every span "
                "for that item would be matched against only one of the transcripts, and "
                "nothing downstream could tell which."
            )
        transcripts[item.item_id] = item.transcript_th
    if not transcripts:
        raise EvidenceError(f"{testset_path} holds no items")
    return transcripts


def _check_provenance(
    meta: dict, testset_path: Path, *, require_recorded_sha: bool
) -> tuple[bool, str | None]:
    """Refuse to score a run against a testset it did not see.

    Returns `(verified, actual_sha)`. The same guard `cli.py:1159` applies before
    scoring, for the same reason: matching model output against a transcript the model
    was never shown yields a number that looks exactly like a real low verbatim rate,
    and would be read as the model inventing its evidence.

    `(False, None)` when `run.json` is absent or records no sha -- synthetic run
    directories in tests have neither, and refusing them would force every test to carry
    a sha it does not otherwise need.

    `require_recorded_sha=False` downgrades a genuine mismatch from a raise to
    `(False, actual)`. See the module docstring for the one case that is for, and for
    what it costs. The actual sha is returned rather than discarded precisely because
    the caller has now taken on the obligation to show the transcripts have not moved,
    and cannot do that without knowing which file was read.
    """
    recorded = meta.get("testset_sha")
    if not recorded:
        return False, None
    actual = testset_sha(testset_path)
    if actual == recorded:
        return True, actual
    if not require_recorded_sha:
        return False, actual
    raise EvidenceError(
        f"{testset_path} is sha {actual}, but the run recorded {recorded}. These "
        "keyword spans were produced against a different version of this file, so every "
        "count would be a comparison against text the model may never have seen. Pass "
        "the testset the run actually used. If the edit was to the label side only "
        "(`gt`, `evidence`, `rules`), which cannot move any count here, "
        "require_recorded_sha=False proceeds and records the mismatch in the result -- "
        "and it then falls to you to show `transcript_th` has not moved."
    )
