"""Tests for the workbook-specific evaluation layer.

The end-to-end tests run against ``debug/model_comparison.xlsx`` with a stub embedder, so the
whole scoring path is exercised on real data without SharePoint or Vertex credentials. That
artifact currently holds a Google run's sheets, so those classes skip until a workbook with
a ``Voice - Internal Model Result`` sheet is placed there.
"""

import io
from pathlib import Path

import pandas as pd
import pytest
from openpyxl import load_workbook

from src.local_model.sentiment.internal_confusion_matrix import (
    BINARY_CRITERIA,
    BLOCK_CALL_TYPE_SUMMARY,
    BLOCK_LEGEND,
    BLOCK_OVERALL,
    BLOCK_QA,
    BLOCK_SENTIMENT,
    BLOCK_SUMMARY,
    BLOCK_TRANSCRIPT,
    CALL_TYPE_LABELS,
    FAMILY_TRANSCRIPT,
    KEY_COLUMN,
    NOT_APPLICABLE,
    QA_CRITERIA,
    SENTIMENT_COLUMNS,
    UNTABLED_BLOCKS,
    Config,
    _is_count_column,
    _is_inverted_metric,
    _is_ratio_column,
    _is_ratio_metric,
    chunk_text,
    embed_pooled,
    evaluate,
    parse_call_types,
    resolve_sheet_name,
    write_dashboard,
)

WORKBOOK = Path(__file__).resolve().parents[1] / "debug" / "model_comparison.xlsx"
TRANSCRIPT_DIR = Path(__file__).resolve().parents[1] / "debug"
GT_SHEET = "Voice - Groundtruth"
RESULT_SHEET = "Voice - Internal Model Result"

READ_KWARGS = {"dtype": str, "header": 0, "keep_default_na": False, "na_values": [""]}

# Deterministic and dimension-stable: every text embeds to the same vector, so every cosine is
# exactly 1.0. These tests assert on the join, the label handling and the block shapes -- the
# similarity maths has its own tests in test_metrics.py.
def stub_embedder(texts):
    return [[1.0, 0.0, 0.0] for _ in texts]


@pytest.fixture(scope="module")
def sheets():
    if not WORKBOOK.exists():
        pytest.skip(f"sample workbook not present: {WORKBOOK}")
    book = pd.ExcelFile(WORKBOOK, engine="openpyxl")
    if RESULT_SHEET not in book.sheet_names:
        pytest.skip(f"sheet not present in sample workbook: {RESULT_SHEET}")
    return book.parse(GT_SHEET, **READ_KWARGS), book.parse(RESULT_SHEET, **READ_KWARGS)


@pytest.fixture(scope="module")
def blocks(sheets):
    gt_df, result_df = sheets
    return evaluate(gt_df, result_df, embedder=stub_embedder, threshold=0.80)


@pytest.fixture(scope="module")
def sheet(blocks):
    """The written dashboard, reloaded so the styling can be inspected as Excel will see it."""
    updated = write_dashboard(WORKBOOK.read_bytes(), blocks, "Evaluation Dashboard")
    return load_workbook(io.BytesIO(updated))["Evaluation Dashboard"]


@pytest.fixture(scope="module")
def transcripts():
    """The 300 sample transcripts as ground truth, with a plausible ASR divergence as the model's.

    Real Thai text at real lengths -- the median needs two embedding chunks and the longest needs
    thirteen, which is the whole reason chunking exists.
    """
    files = sorted(TRANSCRIPT_DIR.glob("*.txt"))
    if not files:
        pytest.skip(f"sample transcripts not present: {TRANSCRIPT_DIR}")
    gt = {path.stem: path.read_text(encoding="utf-8") for path in files}
    return gt, {stem: text.replace("ค่ะ", "คะ") for stem, text in gt.items()}


@pytest.fixture(scope="module")
def transcript_blocks(sheets, transcripts):
    gt_df, result_df = sheets
    gt_transcripts, pred_transcripts = transcripts
    return evaluate(
        gt_df,
        result_df,
        embedder=stub_embedder,
        gt_transcripts=gt_transcripts,
        pred_transcripts=pred_transcripts,
    )


def open_workbook(workbook_byte):
    """pandas needs a file-like object, not raw bytes."""
    return pd.ExcelFile(io.BytesIO(workbook_byte), engine="openpyxl")


def frame_row(frame, column, value):
    """Return the single row where ``frame[column] == value``."""
    matches = frame[frame[column] == value]
    assert len(matches) == 1, f"expected one {value!r} row, got {len(matches)}"
    return matches.iloc[0]


class TestParseCallTypes:
    def test_order_and_spacing_are_normalised_away(self):
        assert parse_call_types("Enquiry, Service Request")[0] == parse_call_types(
            "Service Request,Enquiry"
        )[0]

    def test_case_is_normalised_to_the_canonical_spelling(self):
        labels, unknown = parse_call_types("service request")

        assert labels == {"Service Request"}
        assert unknown == []

    def test_unknown_token_is_reported_not_folded_into_a_neighbour(self):
        labels, unknown = parse_call_types("Complaint,Downsell")

        # The rest of the row is still scored -- one bad token must not discard a correct label.
        assert labels == {"Complaint"}
        # And "Downsell" is surfaced rather than guessed into Retention.
        assert unknown == ["Downsell"]

    def test_sales_is_not_silently_mapped_to_sale(self):
        labels, unknown = parse_call_types("Sales")

        assert labels == set()
        assert unknown == ["Sales"]

    def test_blank_and_empty_segments_are_ignored(self):
        assert parse_call_types("") == (set(), [])
        assert parse_call_types("Enquiry,,")[0] == {"Enquiry"}
        assert parse_call_types(None) == (set(), [])


class TestResolveSheetName:
    def test_returns_the_base_name_when_free(self):
        assert resolve_sheet_name(["Sheet1"], "Evaluation Dashboard") == "Evaluation Dashboard"

    def test_first_collision_takes_suffix_one(self):
        existing = ["Sheet1", "Evaluation Dashboard"]

        assert resolve_sheet_name(existing, "Evaluation Dashboard") == "Evaluation Dashboard_1"

    def test_skips_over_taken_suffixes(self):
        existing = ["Evaluation Dashboard", "Evaluation Dashboard_1", "Evaluation Dashboard_2"]

        assert resolve_sheet_name(existing, "Evaluation Dashboard") == "Evaluation Dashboard_3"


class TestConfigWorkbookPath:
    def test_prefix_is_inserted_as_a_folder(self):
        config = Config(run_prefix="2026-08-06_10-00-00")

        assert config.workbook_path == (
            "/poc_internal_model_migration/voicefiles_internal_output/"
            "2026-08-06_10-00-00/model_comparison.xlsx"
        )

    def test_blank_prefix_does_not_leave_a_double_slash(self):
        # run_prefix="" spelled out rather than taken from the default: the default is documented
        # as hand-filled before each run, so a test relying on it fails every time someone does
        # the thing the comment tells them to.
        assert Config(run_prefix="").workbook_path == (
            "/poc_internal_model_migration/voicefiles_internal_output/model_comparison.xlsx"
        )


class TestEvaluateOnSyntheticData:
    def build(self, **columns):
        """Build a minimal pair of sheets carrying every column evaluate() reads."""
        rows = len(next(iter(columns.values())))
        base = {KEY_COLUMN: [f"call_{i}.wav" for i in range(rows)]}
        base.update({column: ["Meet"] * rows for column in QA_CRITERIA})
        base.update({column: ["Neutral"] * rows for column in SENTIMENT_COLUMNS})
        base["call_type"] = ["Enquiry"] * rows
        base["summary_story"] = ["a summary"] * rows
        base.update(columns)
        return pd.DataFrame(base)

    def test_stray_sentiment_value_is_excluded_without_raising(self):
        gt = self.build(overall_sentiment=["Neutral", "a Thai narrative, not a label", "Positive"])
        result = self.build(overall_sentiment=["Neutral", "Neutral", "Positive"])

        blocks = evaluate(gt, result, embedder=stub_embedder)
        row = frame_row(blocks[BLOCK_SENTIMENT], "Column", "overall_sentiment")

        assert row["Scored"] == 2
        assert row["Excluded"] == 1
        # The two scorable rows both agree, so the exclusion is not disguised as a model error.
        assert row["Accuracy"] == 1.0

    def test_na_is_scored_as_a_class_on_a_ternary_column(self):
        gt = self.build(ending_standard=["N/A", "N/A", "Meet"])
        result = self.build(ending_standard=["N/A", "N/A", "N/A"])

        blocks = evaluate(gt, result, embedder=stub_embedder)
        row = frame_row(blocks[BLOCK_QA], "Column", "ending_standard")

        assert row["Excluded"] == 0
        assert row["Scored"] == 3
        assert row["N/A Support"] == 2
        assert row["N/A TP"] == 2
        # The sheet stores 4 decimal places, so the tolerance is the rounding, not the maths.
        assert row["Accuracy"] == pytest.approx(2 / 3, abs=5e-5)

    def test_binary_column_reports_na_group_as_not_applicable(self):
        gt = self.build(manners=["Meet", "Below", "Meet"])
        result = self.build(manners=["Meet", "Meet", "Meet"])

        blocks = evaluate(gt, result, embedder=stub_embedder)
        row = frame_row(blocks[BLOCK_QA], "Column", "manners")

        # Not 0 -- a zero would read as "the model never got N/A right" on a column where N/A is
        # not a legal answer at all.
        for metric in ("TP", "TN", "FP", "FN", "Precision", "Recall", "F1", "Support"):
            assert row[f"N/A {metric}"] == NOT_APPLICABLE
        assert row["Meet Support"] == 2

    def test_unknown_call_type_token_is_dropped_and_counted(self):
        gt = self.build(call_type=["Complaint,Downsell", "Enquiry"])
        result = self.build(call_type=["Complaint", "Enquiry"])

        blocks = evaluate(gt, result, embedder=stub_embedder)
        summary = blocks[BLOCK_CALL_TYPE_SUMMARY]

        assert frame_row(summary, "Metric", "Unknown tokens dropped")["Value"] == 1
        # Dropping the token leaves {Complaint}, which the result matches exactly.
        assert frame_row(summary, "Metric", "Exact set match")["Value"] == 1.0

    def test_blank_summary_on_either_side_is_excluded(self):
        gt = self.build(summary_story=["a summary", "", "another"])
        result = self.build(summary_story=["a summary", "something", "another"])

        blocks = evaluate(gt, result, embedder=stub_embedder)
        summary = blocks[BLOCK_SUMMARY]

        assert frame_row(summary, "Metric", "Scored")["Value"] == 2
        assert frame_row(summary, "Metric", "Excluded (blank either side)")["Value"] == 1

    def test_short_embedder_result_raises_rather_than_misaligning_rows(self):
        gt = self.build(summary_story=["one", "two"])
        result = self.build(summary_story=["one", "two"])

        with pytest.raises(ValueError, match="vectors for"):
            evaluate(gt, result, embedder=lambda texts: [[1.0]] * (len(texts) - 1))

    def test_missing_key_column_raises(self):
        gt = self.build(summary_story=["one"]).drop(columns=[KEY_COLUMN])
        result = self.build(summary_story=["one"])

        with pytest.raises(KeyError, match="Voice File Name"):
            evaluate(gt, result, embedder=stub_embedder)


class TestEvaluateOnSampleWorkbook:
    """Regression anchors computed independently from the sample workbook."""

    def test_join_covers_the_overlapping_rows_only(self, blocks):
        overall = blocks[BLOCK_OVERALL]

        assert set(overall["Rows"]) == {298}

    def test_every_qa_column_scores_all_matched_rows(self, blocks):
        qa = blocks[BLOCK_QA]

        assert len(qa) == len(QA_CRITERIA)
        assert set(qa["Scored"]) == {298}
        assert set(qa["Excluded"]) == {0}

    def test_call_type_matches_the_independently_computed_figures(self, blocks):
        summary = blocks[BLOCK_CALL_TYPE_SUMMARY]

        # Computed straight from the sheet under the 5-label rule; if the normalisation drifts,
        # these move.
        assert frame_row(summary, "Metric", "Exact set match")["Value"] == pytest.approx(0.7282)
        assert frame_row(summary, "Metric", "Micro-F1")["Value"] == pytest.approx(0.9057)
        assert frame_row(summary, "Metric", "Micro-Precision")["Value"] == pytest.approx(0.8867)
        assert frame_row(summary, "Metric", "Micro-Recall")["Value"] == pytest.approx(0.9256)

    def test_binary_criteria_never_score_an_na_class(self, blocks):
        qa = blocks[BLOCK_QA]

        for column in BINARY_CRITERIA:
            row = frame_row(qa, "Column", column)
            assert row["N/A Support"] == NOT_APPLICABLE

    def test_all_five_call_types_appear_even_when_rare(self, blocks):
        labels = blocks["CALL TYPE (per label)"]

        assert list(labels["Label"]) == list(CALL_TYPE_LABELS)

    def test_legend_is_the_last_block(self, blocks):
        assert list(blocks)[-1] == BLOCK_LEGEND
        assert list(blocks[BLOCK_LEGEND].columns) == ["Term", "Meaning"]


class TestWriteDashboard:
    def test_appends_without_disturbing_existing_sheets(self, blocks):
        if not WORKBOOK.exists():
            pytest.skip(f"sample workbook not present: {WORKBOOK}")

        original = WORKBOOK.read_bytes()
        updated = write_dashboard(original, blocks, "Evaluation Dashboard")

        book = open_workbook(updated)

        assert book.sheet_names == [GT_SHEET, RESULT_SHEET, "Evaluation Dashboard"]
        # The source sheets survive intact -- this writes into the original bytes rather than
        # re-serialising frames read back out of them.
        assert len(book.parse(GT_SHEET, **READ_KWARGS)) == 300
        assert len(book.parse(RESULT_SHEET, **READ_KWARGS)) == 298

    def test_resolved_name_lets_a_second_run_coexist(self, blocks):
        if not WORKBOOK.exists():
            pytest.skip(f"sample workbook not present: {WORKBOOK}")

        once = write_dashboard(WORKBOOK.read_bytes(), blocks, "Evaluation Dashboard")
        existing = open_workbook(once).sheet_names
        twice = write_dashboard(once, blocks, resolve_sheet_name(existing, "Evaluation Dashboard"))

        assert "Evaluation Dashboard_1" in open_workbook(twice).sheet_names

    def test_refuses_to_overwrite_an_existing_sheet(self, blocks):
        if not WORKBOOK.exists():
            pytest.skip(f"sample workbook not present: {WORKBOOK}")

        once = write_dashboard(WORKBOOK.read_bytes(), blocks, "Evaluation Dashboard")

        # The backstop: reusing the name discards a previous evaluation, so it must not be
        # possible by accident even though the blocks all share one sheet.
        with pytest.raises(ValueError, match="already exists"):
            write_dashboard(once, blocks, "Evaluation Dashboard")


class TestColumnClassification:
    """The number format and the colour scale both hang off these two predicates."""

    @pytest.mark.parametrize(
        "name", ["Accuracy", "Macro-F1", "Precision", "Recall", "F1", "Meet F1", "Positive Recall"]
    )
    def test_ratio_columns_are_recognised_bare_and_per_class(self, name):
        assert _is_ratio_column(name)
        assert not _is_count_column(name)

    @pytest.mark.parametrize(
        "name", ["Scored", "Excluded", "Rows", "Columns", "TP", "Meet TP", "N/A Support"]
    )
    def test_count_columns_are_recognised_bare_and_per_class(self, name):
        assert _is_count_column(name)
        # A count must never be colour-scaled on a 0-1 range: 298 would paint solid green.
        assert not _is_ratio_column(name)

    @pytest.mark.parametrize("name", ["Column", "Label", "Metric", "Note", "Family"])
    def test_identity_columns_are_neither(self, name):
        assert not _is_ratio_column(name)
        assert not _is_count_column(name)

    def test_metric_rows_are_classified_by_name_not_position(self):
        assert _is_ratio_metric("Exact set match")
        assert _is_ratio_metric("Mean cosine")
        # The threshold travels in the label, so this one matches by prefix.
        assert _is_ratio_metric("Pass rate (>= 0.80)")
        assert not _is_ratio_metric("Scored")
        assert not _is_ratio_metric("Unknown tokens dropped")


class TestDashboardStyling:
    def test_every_data_block_becomes_a_table(self, sheet, blocks):
        expected = len(blocks) - len(UNTABLED_BLOCKS)

        assert len(sheet.tables) == expected
        assert all(t.tableStyleInfo.showRowStripes for t in sheet.tables.values())

    def test_table_ranges_do_not_overlap(self, sheet):
        spans = sorted(
            (int(ref.split(":")[0].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")),
             int(ref.split(":")[1].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")))
            for ref in (t.ref for t in sheet.tables.values())
        )

        # Excel refuses to open a workbook whose tables overlap, so this is a load-bearing check
        # rather than a cosmetic one.
        for (_, end), (start, _) in zip(spans, spans[1:], strict=False):
            assert start > end

    def test_table_names_are_namespaced_by_sheet(self, sheet):
        # Table names are workbook-scoped: a second evaluation writing "Evaluation Dashboard_1"
        # must not collide with the tables the first run left behind.
        assert all(name.startswith("t_Evaluation_Dashboard_") for name in sheet.tables)

    def test_scores_carry_a_colour_scale_and_counts_do_not(self, sheet):
        scaled = {str(rule.sqref) for rule in sheet.conditional_formatting}

        # D and E are Accuracy and Macro-F1 in the QA block (header row 13, data 14-35).
        assert "D14:D35" in scaled
        assert "E14:E35" in scaled
        # B is Scored in the same block.
        assert "B14:B35" not in scaled

    def test_not_applicable_cells_are_muted_rather_than_zero(self, sheet):
        # manners is binary, so its whole N/A group is n/a. Row 15, N/A group starts at V.
        cell = sheet["V15"]

        assert cell.value == NOT_APPLICABLE
        assert cell.font.italic
        assert cell.font.color.rgb.endswith("808080")

    def test_ratios_and_counts_get_distinct_number_formats(self, sheet):
        assert sheet["D14"].number_format == "0.0000"  # Accuracy
        assert sheet["B14"].number_format == "0"  # Scored
        assert sheet["F14"].number_format == "0"  # Meet TP

    def test_mixed_metric_value_column_is_formatted_per_row(self, sheet):
        # The CALL TYPE block puts ratios and counts in one column, so the format is decided off
        # each row's metric name.
        assert sheet["A45"].value == "Exact set match"
        assert sheet["B45"].number_format == "0.0000"
        assert sheet["A50"].value == "Scored"
        assert sheet["B50"].number_format == "0"


class TestChunkText:
    def test_short_text_is_one_chunk(self):
        assert chunk_text("Agent: hello\nCustomer: hi\n", 100) == ["Agent: hello\nCustomer: hi\n"]

    def test_splits_on_line_boundaries(self):
        text = "Agent: aaaa\nCustomer: bbbb\nAgent: cccc\n"

        chunks = chunk_text(text, 20)

        # Every chunk ends at a newline, so no speaker turn is cut in half -- a fragment starting
        # mid-sentence embeds to a meaning its half of the turn does not carry.
        assert all(chunk.endswith("\n") for chunk in chunks)
        assert all(len(chunk) <= 20 for chunk in chunks)

    def test_chunks_reassemble_to_the_input(self):
        text = "".join(f"Agent: line {i} of the call\n" for i in range(40))

        assert "".join(chunk_text(text, 100)) == text

    def test_single_over_long_line_is_hard_split(self):
        # No boundary to break on, and an oversized chunk would be rejected by the API, which
        # sends auto_truncate=False precisely so it cannot be silently cut instead.
        chunks = chunk_text("x" * 250, 100)

        assert [len(chunk) for chunk in chunks] == [100, 100, 50]
        assert "".join(chunks) == "x" * 250

    def test_over_long_line_flushes_what_is_already_held(self):
        chunks = chunk_text("short\n" + "y" * 150, 100)

        assert chunks[0] == "short\n"
        assert "".join(chunks) == "short\n" + "y" * 150

    def test_blank_text_yields_no_chunks(self):
        assert chunk_text("", 100) == []
        assert chunk_text("   \n\n  ", 100) == []

    def test_non_positive_limit_raises(self):
        with pytest.raises(ValueError, match="limit must be"):
            chunk_text("anything", 0)

    def test_real_transcripts_need_multiple_chunks(self, transcripts):
        gt, _ = transcripts
        counts = [len(chunk_text(text, 2000)) for text in gt.values()]

        # The premise of the whole design: most of this corpus does not fit in one request.
        assert max(counts) == 13
        assert sum(1 for n in counts if n > 1) / len(counts) > 0.5


class TestEmbedPooled:
    def test_one_call_covers_every_chunk_of_every_text(self):
        seen = []

        def embedder(texts):
            seen.append(len(texts))
            return [[1.0, 0.0] for _ in texts]

        embed_pooled(["a" * 250, "b" * 100], embedder=embedder, chunk_chars=100)

        # One fan-out for the whole block, not one per text: 3 chunks + 1 chunk in a single call.
        assert seen == [4]

    def test_chunk_count_is_reported(self):
        _, chunks = embed_pooled(
            ["a" * 250, "b" * 100], embedder=lambda t: [[1.0, 0.0]] * len(t), chunk_chars=100
        )

        assert chunks == 4

    def test_chunks_are_resplit_onto_the_right_texts(self):
        # First text takes 3 chunks pointing along x, second takes 1 pointing along y. If the
        # re-split were off by one, the second text's vector would carry some x.
        vectors = [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]

        pooled, _ = embed_pooled(
            ["a" * 250, "b" * 100], embedder=lambda _t: vectors, chunk_chars=100
        )

        assert pooled[0] == pytest.approx([1.0, 0.0])
        assert pooled[1] == pytest.approx([0.0, 1.0])

    def test_wrong_vector_count_raises_rather_than_misaligning(self):
        with pytest.raises(ValueError, match="vectors for"):
            embed_pooled(
                ["a" * 250], embedder=lambda t: [[1.0]] * (len(t) - 1), chunk_chars=100
            )

    def test_blank_text_raises(self):
        with pytest.raises(ValueError, match="blank text at position 1"):
            embed_pooled(["fine", "  "], embedder=lambda t: [[1.0]] * len(t), chunk_chars=100)

    def test_empty_input_costs_no_call(self):
        assert embed_pooled([], embedder=lambda _t: pytest.fail("called"), chunk_chars=100) == (
            [],
            0,
        )


class TestTranscriptBlock:
    def build(self, keys):
        """A minimal pair of sheets carrying only what evaluate() needs to join and score."""
        base = {
            KEY_COLUMN: keys,
            "call_type": ["Enquiry"] * len(keys),
            "summary_story": ["a summary"] * len(keys),
        }
        base.update({column: ["Meet"] * len(keys) for column in QA_CRITERIA})
        base.update({column: ["Neutral"] * len(keys) for column in SENTIMENT_COLUMNS})
        return pd.DataFrame(base)

    def test_block_is_absent_when_transcripts_are_not_supplied(self, blocks):
        # The existing offline path must keep working untouched: a caller that cannot reach the
        # transcript folders still gets the other four families.
        assert BLOCK_TRANSCRIPT not in blocks
        assert FAMILY_TRANSCRIPT not in set(blocks[BLOCK_OVERALL]["Family"])

    def test_block_appears_when_both_sides_are_supplied(self, transcript_blocks):
        assert BLOCK_TRANSCRIPT in transcript_blocks
        # Still last, so the legend stays at the bottom of the sheet.
        assert list(transcript_blocks)[-1] == BLOCK_LEGEND

    def test_one_sided_transcripts_do_not_emit_the_block(self):
        frame = self.build(["call_0.wav"])

        blocks = evaluate(frame, frame, embedder=stub_embedder, gt_transcripts={"call_0": "hi"})

        assert BLOCK_TRANSCRIPT not in blocks

    def test_a_stem_present_on_one_side_only_is_excluded_not_scored(self):
        frame = self.build(["call_0.wav", "call_1.wav"])

        blocks = evaluate(
            frame,
            frame,
            embedder=stub_embedder,
            gt_transcripts={"call_0": "Agent: hello", "call_1": "Agent: hi"},
            pred_transcripts={"call_0": "Agent: hello"},
        )
        block = blocks[BLOCK_TRANSCRIPT]

        assert frame_row(block, "Metric", "Scored")["Value"] == 1
        assert frame_row(block, "Metric", "Excluded (missing either side)")["Value"] == 1

    def test_sheet_key_finds_a_transcript_keyed_without_the_extension(self):
        # Both sheets spell the key "<call>.wav"; internal_asr_llm_output.py writes the transcript as
        # "<call>.txt". Without the stem, no sheet row would ever find its transcript.
        frame = self.build(["call_0.wav"])

        blocks = evaluate(
            frame,
            frame,
            embedder=stub_embedder,
            gt_transcripts={"call_0": "Agent: hello"},
            pred_transcripts={"call_0": "Agent: hello"},
        )

        assert frame_row(blocks[BLOCK_TRANSCRIPT], "Metric", "Scored")["Value"] == 1

    def test_identical_transcripts_score_zero_cer(self):
        frame = self.build(["call_0.wav"])
        text = "Agent: สวัสดีค่ะ\nCustomer: ค่ะ"

        blocks = evaluate(
            frame,
            frame,
            embedder=stub_embedder,
            gt_transcripts={"call_0": text},
            pred_transcripts={"call_0": text},
        )
        block = blocks[BLOCK_TRANSCRIPT]

        assert frame_row(block, "Metric", "Mean CER")["Value"] == 0.0
        assert frame_row(block, "Metric", "Mean char accuracy (1 - CER)")["Value"] == 1.0

    def test_sample_corpus_anchors(self, transcript_blocks):
        block = transcript_blocks[BLOCK_TRANSCRIPT]

        assert frame_row(block, "Metric", "Scored")["Value"] == 298
        assert frame_row(block, "Metric", "Excluded (missing either side)")["Value"] == 0
        # 298 pairs of the same texts at 2000 chars each: 658 chunks per side. If the chunker
        # changes, this moves -- which is the point of pinning it.
        assert frame_row(block, "Metric", "Chunks embedded")["Value"] == 1316
        # The only edit is a one-character substitution, so the rate is small but non-zero.
        assert 0.005 < frame_row(block, "Metric", "Mean CER")["Value"] < 0.02

    def test_overall_row_reports_char_accuracy_not_a_placeholder(self, transcript_blocks):
        row = frame_row(transcript_blocks[BLOCK_OVERALL], "Family", FAMILY_TRANSCRIPT)

        # Transcript is the one added family with a genuine accuracy, so this cell is filled
        # rather than n/a -- and it is 1 - mean CER, on the same 0-1 scale as the rows above it.
        mean_cer = frame_row(transcript_blocks[BLOCK_TRANSCRIPT], "Metric", "Mean CER")["Value"]
        assert row["Accuracy"] == pytest.approx(1.0 - mean_cer, abs=5e-5)
        assert row["Macro-F1"] == NOT_APPLICABLE


class TestCerPresentation:
    """CER is the one metric on the sheet where a lower number is the better one."""

    @pytest.mark.parametrize(
        "name", ["Mean CER", "Median CER", "Best CER (lowest)", "Worst CER (highest)"]
    )
    def test_cer_rows_are_ratios_with_an_inverted_scale(self, name):
        assert _is_ratio_metric(name)
        assert _is_inverted_metric(name)

    def test_cer_pass_rate_is_a_ratio_but_not_inverted(self):
        # As a bare prefix "Pass rate" does not match this, so it would fall through to the
        # integer format and render 0.6141 as 1.
        assert _is_ratio_metric("CER pass rate (<= 0.20)")
        # And a high pass rate is the good outcome, unlike a high CER.
        assert not _is_inverted_metric("CER pass rate (<= 0.20)")

    def test_char_accuracy_takes_the_normal_scale(self):
        assert _is_ratio_metric("Mean char accuracy (1 - CER)")
        assert not _is_inverted_metric("Mean char accuracy (1 - CER)")

    def test_chunk_count_is_a_count_not_a_score(self):
        # 1316 on a 0-1 colour scale paints solid green and reads as a perfect score.
        assert not _is_ratio_metric("Chunks embedded")
        assert not _is_inverted_metric("Chunks embedded")

    def test_inverted_scale_swaps_the_end_colours(self):
        from src.local_model.sentiment.internal_confusion_matrix import (
            SCALE_HIGH,
            SCALE_LOW,
            _colour_scale,
        )

        normal = _colour_scale()
        inverted = _colour_scale(invert=True)

        assert normal.colorScale.color[0].rgb.endswith(SCALE_LOW)
        assert normal.colorScale.color[-1].rgb.endswith(SCALE_HIGH)
        # Reversed, so a CER of 0.04 -- an excellent transcription -- paints green, not red.
        assert inverted.colorScale.color[0].rgb.endswith(SCALE_HIGH)
        assert inverted.colorScale.color[-1].rgb.endswith(SCALE_LOW)


class TestSelfComparisonScoresPerfect(TestEvaluateOnSyntheticData):
    """Feeding one sheet in as both ground truth and result must score 1.0000 everywhere.

    This is the whole-dashboard version of the check that found the macro-F1 fault. The synthetic
    sheets here use only a subset of each label set -- "Meet" but never "Below" or "N/A" -- which
    is the shape the old formula got wrong, and the shape greeting_standard has on the live data.
    """

    def test_every_qa_and_sentiment_score_is_one(self):
        sheet = self.build(
            greeting_standard=["Meet", "Meet", "Below"],
            overall_sentiment=["Neutral", "Positive", "Neutral"],
            call_type=["Enquiry", "Complaint", "Enquiry,Complaint"],
        )

        blocks = evaluate(sheet, sheet.copy(), embedder=stub_embedder)

        for block in (blocks[BLOCK_QA], blocks[BLOCK_SENTIMENT]):
            assert (block["Accuracy"] == 1.0).all()
            assert (block["Macro-F1"] == 1.0).all(), block[["Column", "Macro-F1"]].to_string()

    def test_family_rollup_is_one(self):
        sheet = self.build(greeting_standard=["Meet", "Meet", "Below"])

        blocks = evaluate(sheet, sheet.copy(), embedder=stub_embedder)
        overall = blocks[BLOCK_OVERALL].set_index("Family")

        # 0.9848 and 0.8889 were what the live sheet reported here on a perfect input.
        assert overall.loc["QA Criteria", "Macro-F1"] == 1.0
        assert overall.loc["Sentiment", "Macro-F1"] == 1.0
        assert overall.loc["Call Type", "Macro-F1"] == 1.0

    def test_call_type_scores_one_with_most_labels_unused(self):
        sheet = self.build(call_type=["Enquiry", "Enquiry", "Complaint"])

        blocks = evaluate(sheet, sheet.copy(), embedder=stub_embedder)
        summary = blocks[BLOCK_CALL_TYPE_SUMMARY]

        assert frame_row(summary, "Metric", "Exact set match")["Value"] == 1.0
        assert frame_row(summary, "Metric", "Micro-F1")["Value"] == 1.0
        assert frame_row(summary, "Metric", "Macro-F1")["Value"] == 1.0
        # Sale and Retention never came up, so they are not in the average.
        assert frame_row(summary, "Metric", "Macro-F1 classes")["Value"] == 2


class TestUnusedClassPresentation(TestEvaluateOnSyntheticData):
    def test_class_neither_side_used_blanks_its_scores_but_keeps_its_counts(self):
        gt = self.build(ending_standard=["Meet", "Meet", "Below"])
        result = self.build(ending_standard=["Meet", "Meet", "Meet"])

        blocks = evaluate(gt, result, embedder=stub_embedder)
        row = frame_row(blocks[BLOCK_QA], "Column", "ending_standard")

        # Neither side ever said N/A. A 0.0000 here paints a red cell on a column where N/A was
        # never in play -- the misreading that made greeting_standard look like a model failure.
        for metric in ("Precision", "Recall", "F1"):
            assert row[f"N/A {metric}"] == NOT_APPLICABLE
        # The counts stay real: they are what shows *why* the scores are blank.
        assert row["N/A Support"] == 0
        assert row["N/A TP"] == 0
        assert row["N/A TN"] == 3
        assert row["Macro-F1 classes"] == 2

    def test_class_the_model_invented_keeps_a_zero_score(self):
        gt = self.build(ending_standard=["Meet", "Meet", "Meet"])
        result = self.build(ending_standard=["Meet", "Meet", "N/A"])

        blocks = evaluate(gt, result, embedder=stub_embedder)
        row = frame_row(blocks[BLOCK_QA], "Column", "ending_standard")

        # Support 0 but FP 1: the human never graded N/A and the model produced it anyway. Blanking
        # this would hand the model a free pass for a grade it made up.
        assert row["N/A Support"] == 0
        assert row["N/A FP"] == 1
        assert row["N/A F1"] == 0.0
        assert row["Macro-F1 classes"] == 2

    def test_macro_f1_classes_is_a_count_not_a_score(self):
        # A 2 or 3 on the 0-1 ratio scale paints solid green and reads as a perfect result. Same
        # trap as "Chunks embedded", and the prefix makes it easy to fall into.
        assert _is_count_column("Macro-F1 classes")
        assert not _is_ratio_column("Macro-F1 classes")
        assert not _is_ratio_metric("Macro-F1 classes")


class TestConfigRunFolder:
    def test_workbook_and_transcripts_share_one_run_folder(self):
        config = Config(run_prefix="2026-08-06_10-00-00")

        # internal_asr_llm_output.py writes both into the same folder, so they must not resolve the prefix
        # separately.
        assert config.workbook_path.startswith(config.run_folder + "/")
        assert config.run_folder.endswith("/2026-08-06_10-00-00")

    def test_blank_prefix_does_not_leave_a_double_slash(self):
        assert Config(run_prefix="").run_folder == "/poc_internal_model_migration/voicefiles_internal_output"

    def test_block_titles_are_banners(self, sheet):
        title = sheet["A1"]

        assert title.value == "COMPARISON"
        assert title.font.bold
        assert title.font.color.rgb.endswith("FFFFFF")
        assert title.fill.fgColor.rgb.endswith("1F3864")

    def test_legend_rows_are_merged_wrapped_and_sized(self, sheet, blocks):
        merged = {str(r) for r in sheet.merged_cells.ranges}

        # One merge per legend row, and nothing else on the sheet is merged -- a table cannot
        # contain a merged cell, so a stray merge would break the block above it.
        assert len(merged) == len(blocks[BLOCK_LEGEND])
        row = int(next(iter(merged)).split(":")[0].lstrip("AB"))
        assert sheet.cell(row=row, column=2).alignment.wrap_text
        assert sheet.row_dimensions[row].height >= 15
