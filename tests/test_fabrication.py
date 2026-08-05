"""Gates on `fabrication.fabrication_rate`, starting with the two numbers it must return.

The helper exists to decide whether `v9_16_e1` worked, so the first thing it has to do is
reproduce the baseline it will be compared against. Both committed run directories are
scored here, and the expected values are written out rather than computed, because a test
that recomputes the thing under test agrees with it by construction:

    20260804-222943Z-incumbent   gemini-2.5-flash   39 invented / 15 example   0.3846
    20260804-224050Z-candidate   qwen3.6-27b        29 invented / 18 example   0.6207

If either moves, the helper changed, not the baseline — with one exception, already spent:
these counts are a measurement of frozen run logs AGAINST A GROUND TRUTH, so correcting the
ground truth legitimately moves them. That happened once, on 2026-08-05, and is recorded in
the two baseline docstrings below. The run logs were not touched and cannot be; the numbers
above were re-derived from them, not adjusted until the suite went green.

The rest of the file is about the ways a counter like this is wrong while still printing
a number: counting slots the ground truth filled, counting predictions that join no
ground-truth row, reading payloads the run already filed as unparseable, or measuring
against the wrong example. Each has its own test, and the two mutation tests below pin
that the counter can move at all — a `fabrication_rate` that returned zeros would satisfy
"no fabrication" forever.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen.fabrication import (  # noqa: E402
    EXAMPLE_REASONS,
    FABRICATION_SLOTS,
    FabricationError,
    fabrication_rate,
)
from evalgen.prompts import PROMPTS  # noqa: E402

GT_PATH = ROOT / "tests" / "fixtures" / "testsets" / "retention_v1.gt.csv"
INCUMBENT = ROOT / "out" / "runs" / "20260804-222943Z-incumbent"
CANDIDATE = ROOT / "out" / "runs" / "20260804-224050Z-candidate"

# The runs are working artifacts, not fixtures: they are gitignored, they cost money to
# make, and CI has neither. Skipping is honest; synthesising a stand-in and calling the
# baseline reproduced would not be.
needs_runs = pytest.mark.skipif(
    not (INCUMBENT / "run.jsonl").exists() or not (CANDIDATE / "run.jsonl").exists(),
    reason="the 2026-08-04 run directories are not present (out/ is gitignored)",
)


# ------------------------------------------------------------------ the baseline


@needs_runs
def test_reproduces_the_gemini_baseline() -> None:
    """39 invented, 15 of them example values, 38%.

    Was 42 / 18 / 42% until 2026-08-05, when RET-11's ground truth gained
    `secondary=dissatisfied service` (`retention_v1.jsonl:11`, licensed by
    `prompt.py:4361`, evidence span `ไม่มีใครตามเรื่องเลย`). The run log was not touched.
    What moved is what the log is measured against: `FABRICATION_SLOTS` is
    (`secondary`, `third`) (`fabrication.py:85`) and a slot only counts as invented into
    while the GROUND TRUTH left it blank (`fabrication.py:149-151`), so 5011's secondary
    stopped being a blank slot.

    Re-derived from the log rather than back-fitted to the assertion: at call 5011,
    product Postpaid, all 3 replicates are `parse_ok` and each puts exactly one label in
    `secondary` — `dissatisfied service`, which is in `EXAMPLE_REASONS`. So both counters
    lose the same 3: invented 42 - 3 = 39, example_derived 18 - 3 = 15. `third` at 5011 is
    empty in all 3 replicates and 5011's gt `third` stays blank, so nothing else moves.
    """
    result = fabrication_rate(INCUMBENT, GT_PATH)
    assert result["model_requested"] == "google/gemini-2.5-flash"
    assert result["prompt_id"] == "v9_16_base"
    assert result["invented"] == 39
    assert result["example_derived"] == 15
    assert result["rate"] == pytest.approx(15 / 39, abs=1e-6)
    # 0.38461..., quoted as "38%" by truncation rather than rounding. The pair 15/39 is
    # what is load-bearing; the percentage is how it was written down.
    assert int(result["rate"] * 100) == 38


@needs_runs
def test_reproduces_the_qwen_baseline() -> None:
    """29 invented, 18 of them example values, 62% -- and `save cost` alone is still 13.

    The `save cost` line is the finding this whole variant rests on: a single value from
    the worked example accounts for 45% of everything the arm invented.

    Was 30 / 19 / 63% until the 2026-08-05 RET-11 ground-truth correction described in
    `test_reproduces_the_gemini_baseline`. Re-derived the same way, from the same log:
    at call 5011 / Postpaid this run has only ONE `parse_ok` replicate (the other two are
    among its 10 skipped records, pinned below), and that one replicate puts
    `dissatisfied service` in `secondary`. So this arm loses 1 where Gemini lost 3:
    invented 30 - 1 = 29, example_derived 19 - 1 = 18.

    `save cost` is untouched at 13, measured, which matters more than the headline: the
    correction moved the denominator, not the finding the variant was built to test.
    """
    result = fabrication_rate(CANDIDATE, GT_PATH)
    assert result["model_requested"] == "qwen/qwen3.6-27b"
    assert result["invented"] == 29
    assert result["example_derived"] == 18
    assert result["rate"] == pytest.approx(18 / 29, abs=1e-6)
    assert round(result["rate"] * 100) == 62
    assert result["emitted"]["save cost"] == 13


@needs_runs
@pytest.mark.parametrize(
    ("run_dir", "main_slot", "skipped"),
    [(INCUMBENT, 3, 0), (CANDIDATE, 4, 10)],
)
def test_the_diagnostics_the_headline_must_not_be_read_without(
    run_dir: Path, main_slot: int, skipped: int
) -> None:
    """`main` is counted, separately; orphans and skipped records are reported, not hidden.

    `main` is out of the headline because `v9_16_e1` does not touch its example value —
    folding it in would mix a number the experiment can move with one it cannot. On both
    baseline runs every `main`-slot fabrication is the label `other`, which is NOT an
    example value, so nothing example-derived is being hidden by the split.
    """
    result = fabrication_rate(run_dir, GT_PATH)
    assert result["slots"] == FABRICATION_SLOTS == ("secondary", "third")
    assert result["main_slot_invented"] == main_slot
    assert set(result["main_slot_emitted"]) == {"other"}
    assert not set(result["main_slot_emitted"]) & EXAMPLE_REASONS

    # RET-18: the ground truth calls the product `unknown`, both models answered
    # `Postpaid`. Those rows join nothing and cannot be asked about GT blankness.
    assert result["orphan_product_rows"] == 3

    assert result["records_read"] == 60
    assert result["records_skipped_not_parse_ok"] == skipped
    assert result["invented"] == sum(result["emitted"].values())
    assert result["blank_slots_offered"] >= result["invented"]


@needs_runs
def test_the_headline_is_not_silently_a_count_of_wrong_labels() -> None:
    """Some invented labels are correct labels in the wrong slot, and that is reported.

    Blankness is read per slot, which is the grain the hypothesis is about; the scorer
    reads reasons as a rank-free set. `also_present_in_gt_row` is the gap between the
    two, returned so nobody reads `invented` as "labels the ground truth rejects".
    """
    for run_dir, expected in ((INCUMBENT, 9), (CANDIDATE, 10)):
        result = fabrication_rate(run_dir, GT_PATH)
        assert result["also_present_in_gt_row"] == expected
        assert 0 < result["also_present_in_gt_row"] < result["invented"]


# ------------------------------------------------- what the comparison set must be


def test_example_values_come_from_the_base_prompt_not_the_variant() -> None:
    """Scoring an e1 run against e1's own blanked example would report 0 by construction.

    That is the single way this helper could declare the experiment a success without
    the model changing anything, so it is pinned rather than left to the docstring.
    """
    assert EXAMPLE_REASONS == frozenset({"network", "save cost", "dissatisfied service"})
    base_text = PROMPTS["v9_16_base"].system_text
    e1_text = PROMPTS["v9_16_e1"].system_text
    for value in ("save cost", "dissatisfied service"):
        assert f'"reason": "{value}"' in base_text
        assert f'"reason": "{value}"' not in e1_text
        assert value in EXAMPLE_REASONS  # still the comparison set for an e1 run


# --------------------------------------------------- the counter can move, both ways


def _write_run(tmp_path: Path, records: list[dict]) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    return run_dir


def _record(*, call_id: str = "5001", parse_ok: bool = True, **slots) -> dict:
    """One replicate answering about `Postpaid`, with whichever slots are given."""
    block: dict = {"retention_outcome": "save"}
    for slot, reason in slots.items():
        block[slot] = {"reason": reason, "keyword": "x"}
    return {
        "call_id": call_id,
        "item_id": "SYNTH",
        "replicate": 1,
        "parse_ok": parse_ok,
        "payload": {"product": {"Postpaid": block}},
    }


def test_a_run_that_fabricates_nothing_scores_zero(tmp_path: Path) -> None:
    """5001's ground truth is `main=network`, secondary and third blank.

    A model that fills only `main`, correctly, has invented nothing — and the rate is
    0.0 rather than a division by zero dressed up as `nan`.
    """
    run_dir = _write_run(tmp_path, [_record(main="network")])
    result = fabrication_rate(run_dir, GT_PATH)
    assert result["invented"] == 0
    assert result["example_derived"] == 0
    assert result["rate"] == 0.0
    assert result["emitted"] == {}


def test_a_run_that_copies_the_example_scores_one(tmp_path: Path) -> None:
    """The mutation the whole helper exists to detect: both optional slots, both example."""
    run_dir = _write_run(
        tmp_path,
        [_record(main="network", secondary="save cost", third="dissatisfied service")],
    )
    result = fabrication_rate(run_dir, GT_PATH)
    assert result["invented"] == 2
    assert result["example_derived"] == 2
    assert result["rate"] == 1.0
    assert dict(result["emitted"]) == {"save cost": 1, "dissatisfied service": 1}


def test_invented_and_example_derived_are_different_counters(tmp_path: Path) -> None:
    """A fabricated label that is NOT an example value counts as invented and no more."""
    run_dir = _write_run(
        tmp_path, [_record(main="network", secondary="contract end", third="other")]
    )
    result = fabrication_rate(run_dir, GT_PATH)
    assert result["invented"] == 2
    assert result["example_derived"] == 0
    assert result["rate"] == 0.0


def test_a_slot_the_ground_truth_filled_is_never_invented(tmp_path: Path) -> None:
    """5014's GT is `main=network, secondary=promotion related`, third blank.

    Whatever the model puts in `secondary` there, the GT filled that slot, so it is a
    scored label and not a fabrication. Only `third` can be invented into.
    """
    run_dir = _write_run(
        tmp_path,
        [
            _record(
                call_id="5014",
                main="network",
                secondary="save cost",  # wrong, but into a slot the GT filled
                third="dissatisfied service",
            )
        ],
    )
    result = fabrication_rate(run_dir, GT_PATH)
    assert result["invented"] == 1
    assert dict(result["emitted"]) == {"dissatisfied service": 1}


def test_a_parse_failure_is_not_read(tmp_path: Path) -> None:
    """`parse_ok=False` payloads are for reading, not for scoring (outcomes.py).

    A violation whose payload happens to hold example values must not be counted as
    fabrication: the run already counts it as a parse failure, and counting it twice
    would make one defect look like two.
    """
    run_dir = _write_run(
        tmp_path,
        [
            _record(parse_ok=False, secondary="save cost", third="dissatisfied service"),
            _record(parse_ok=True, main="network"),
        ],
    )
    result = fabrication_rate(run_dir, GT_PATH)
    assert result["invented"] == 0
    assert result["records_read"] == 2
    assert result["records_skipped_not_parse_ok"] == 1


def test_a_comma_split_cell_counts_as_two_labels(tmp_path: Path) -> None:
    """`records.parse_reasons` splits on commas; a fabrication counter that did not
    would disagree with the scorer about what a label is."""
    run_dir = _write_run(
        tmp_path, [_record(main="network", secondary="save cost, other")]
    )
    result = fabrication_rate(run_dir, GT_PATH)
    assert result["invented"] == 2
    assert result["example_derived"] == 1
    assert dict(result["emitted"]) == {"save cost": 1, "other": 1}


def test_a_non_string_reason_is_not_a_label(tmp_path: Path) -> None:
    """Mirrors `flatten._reason`: absent, null, non-object and non-string yield nothing.

    Stringifying a list into the cell would manufacture labels no vocabulary contains,
    and they would be counted here as fabrications the model did not commit.
    """
    run_dir = _write_run(
        tmp_path,
        [
            {
                "call_id": "5001",
                "parse_ok": True,
                "payload": {
                    "product": {
                        "Postpaid": {
                            "secondary": {"reason": None, "keyword": "x"},
                            "third": {"reason": ["save cost"], "keyword": "x"},
                        }
                    }
                },
            }
        ],
    )
    result = fabrication_rate(run_dir, GT_PATH)
    assert result["invented"] == 0


def test_an_unjoined_product_is_reported_rather_than_counted(tmp_path: Path) -> None:
    """A product with no ground-truth row cannot be asked whether a slot was blank."""
    run_dir = _write_run(
        tmp_path,
        [
            {
                "call_id": "5001",
                "parse_ok": True,
                "payload": {
                    "product": {"TVS": {"secondary": {"reason": "save cost", "keyword": ""}}}
                },
            }
        ],
    )
    result = fabrication_rate(run_dir, GT_PATH)
    assert result["invented"] == 0
    assert result["orphan_product_rows"] == 1
    assert result["rows_joined"] == 0


# ------------------------------------------------------------------- loud failures


def test_a_missing_run_log_raises(tmp_path: Path) -> None:
    with pytest.raises(FabricationError) as exc:
        fabrication_rate(tmp_path, GT_PATH)
    assert "run.jsonl" in str(exc.value)


def test_an_empty_run_log_raises(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(FabricationError):
        fabrication_rate(run_dir, GT_PATH)


def test_a_duplicated_join_key_raises(tmp_path: Path) -> None:
    """The (call_id, product) shortcut is only safe while it is unique, so it is checked.

    `run.jsonl` carries no phone number, so this module cannot use the scorer's
    three-part key. A duplicated pair would count one prediction against two ground
    truths and inflate everything downstream of it.
    """
    bad_gt = tmp_path / "gt.csv"
    bad_gt.write_text(
        "call_id,phone_number,product,call_result,main,secondary,third\n"
        "5001,0810000001,postpaid,save,network,,\n"
        "5001,0810000099,postpaid,churn,other,,\n",
        encoding="utf-8",
    )
    run_dir = _write_run(tmp_path, [_record(main="network")])
    with pytest.raises(FabricationError) as exc:
        fabrication_rate(run_dir, bad_gt)
    assert "two rows" in str(exc.value)


def test_a_ground_truth_without_the_reason_columns_raises(tmp_path: Path) -> None:
    """Without the columns, every blank is indistinguishable from every absent value."""
    bad_gt = tmp_path / "gt.csv"
    bad_gt.write_text("call_id,phone_number,product,call_result\n5001,081,postpaid,save\n", encoding="utf-8")
    run_dir = _write_run(tmp_path, [_record(main="network")])
    with pytest.raises(FabricationError) as exc:
        fabrication_rate(run_dir, bad_gt)
    assert "missing column" in str(exc.value)


def test_a_missing_ground_truth_raises(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path, [_record(main="network")])
    with pytest.raises(FabricationError):
        fabrication_rate(run_dir, tmp_path / "nope.csv")
