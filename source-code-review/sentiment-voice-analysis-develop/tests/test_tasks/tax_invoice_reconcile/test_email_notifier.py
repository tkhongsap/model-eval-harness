"""Tests for :class:`EmailNotifier` (tax_invoice_reconcile) — template render + Graph send.

Covers: ``{NAME}`` placeholder substitution via ``str.format``, the newline->``<br>``
HTML conversion, sending with no placeholders at all, the send call being forwarded to
:meth:`MSGraphModule.send_email` with the right recipients, a missing-template file
raising ``FileNotFoundError``, and the :meth:`EmailNotifier.build_fact_check_table` HTML fragment.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.utils.file_utils import load_yaml
from tasks.tax_invoice_reconcile.helper.constant import FIELD_MAPPING
from tasks.tax_invoice_reconcile.module.email_notifier import EmailNotifier

_SHIPPED_BASELINE_PATH = "config/tax_invoice_extraction/fact_check_uat_baseline.yml"

_METRIC_ROWS = [
    {"label": "Tax Invoice Number", "accuracy": 92.31, "precision": 92.31, "recall": 55.55, "f1_score": 77.77},
    {"label": "Buyer Name", "accuracy": 84.62, "precision": 84.62, "recall": 50.0, "f1_score": 61.61},
    {"label": "overall", "accuracy": 88.5, "precision": 88.5, "recall": 44.44, "f1_score": 66.66},
]


def _notifier(tmp_path, msgraph: MagicMock | None = None) -> tuple[EmailNotifier, MagicMock]:
    msgraph = msgraph or MagicMock()
    return EmailNotifier(msgraph=msgraph, template_dir=str(tmp_path)), msgraph


def _baseline_yaml(tmp_path) -> str:
    """Write a UAT-baseline YAML (gt_field -> fraction) and return its path."""
    path = tmp_path / "fact_check_uat_baseline.yml"
    path.write_text("tax_invoice_number: 0.99\nbuyer_name: 0.98\n", encoding="utf-8")
    return str(path)


def test_send_template_substitutes_placeholders_and_converts_newlines(tmp_path):
    # Arrange
    (tmp_path / "report.txt").write_text("Hello {NAME},\nYour report {COUNT} is ready.", encoding="utf-8")
    notifier, msgraph = _notifier(tmp_path)

    # Act
    notifier.send_template(
        "report.txt",
        "Daily Report",
        sender_email="bot@example.com",
        receiver_email="user@example.com",
        NAME="Somchai",
        COUNT=5,
    )

    # Assert
    msgraph.send_email.assert_called_once_with(
        subject="Daily Report",
        body="Hello Somchai,<br>\nYour report 5 is ready.",
        sender_email="bot@example.com",
        receiver_email="user@example.com",
        cc_email=None,
    )


def test_send_template_with_no_placeholders_sends_template_verbatim(tmp_path):
    # Arrange
    (tmp_path / "plain.txt").write_text("No placeholders here.", encoding="utf-8")
    notifier, msgraph = _notifier(tmp_path)

    # Act
    notifier.send_template(
        "plain.txt",
        "Plain",
        sender_email="bot@example.com",
        receiver_email="user@example.com",
    )

    # Assert
    _, kwargs = msgraph.send_email.call_args
    assert kwargs["body"] == "No placeholders here."


def test_send_template_forwards_cc_and_list_recipients(tmp_path):
    # Arrange
    (tmp_path / "cc.txt").write_text("Body", encoding="utf-8")
    notifier, msgraph = _notifier(tmp_path)

    # Act
    notifier.send_template(
        "cc.txt",
        "With CC",
        sender_email="bot@example.com",
        receiver_email=["a@example.com", "b@example.com"],
        cc_email="c@example.com",
    )

    # Assert
    _, kwargs = msgraph.send_email.call_args
    assert kwargs["receiver_email"] == ["a@example.com", "b@example.com"]
    assert kwargs["cc_email"] == "c@example.com"


def test_send_template_missing_file_raises_file_not_found(tmp_path):
    # Arrange
    notifier, _ = _notifier(tmp_path)

    # Act / Assert
    with pytest.raises(FileNotFoundError):
        notifier.send_template(
            "missing.txt",
            "Subject",
            sender_email="bot@example.com",
            receiver_email="user@example.com",
        )


def test_send_template_multiline_body_converts_every_newline(tmp_path):
    # Arrange
    (tmp_path / "multi.txt").write_text("Line1\nLine2\nLine3", encoding="utf-8")
    notifier, msgraph = _notifier(tmp_path)

    # Act
    notifier.send_template(
        "multi.txt",
        "Multi",
        sender_email="bot@example.com",
        receiver_email="user@example.com",
    )

    # Assert
    _, kwargs = msgraph.send_email.call_args
    assert kwargs["body"] == "Line1<br>\nLine2<br>\nLine3"


def test_build_fact_check_table_renders_field_rows_and_excludes_overall(tmp_path):
    # Arrange
    notifier, _ = _notifier(tmp_path)

    # Act
    html = notifier.build_fact_check_table(_METRIC_ROWS, _baseline_yaml(tmp_path))

    # Assert — one <tr> per field row, label + accuracy rendered; the overall row is log-only.
    assert html.count("<tr") == 3  # header + 2 field rows (overall excluded)
    assert '<td class="field-col">Tax Invoice Number</td>' in html
    assert "<td>92.31%</td>" in html
    assert '<td class="field-col">Buyer Name</td>' in html
    assert "<td>84.62%</td>" in html
    assert "overall" not in html
    assert "88.50%" not in html


def test_build_fact_check_table_shows_only_the_accuracy_metric(tmp_path):
    # Arrange
    notifier, _ = _notifier(tmp_path)

    # Act
    html = notifier.build_fact_check_table(_METRIC_ROWS, _baseline_yaml(tmp_path))

    # Assert — header is Field/Baseline/Accuracy; recall/f1 values never leak into the HTML.
    assert "<th>Field</th><th>Baseline</th><th>Accuracy</th>" in html
    for leaked in ("55.55", "77.77", "50.00", "61.61", "F1", "Precision", "Recall"):
        assert leaked not in html


def test_build_fact_check_table_reads_per_field_baseline_from_yaml(tmp_path):
    # Arrange
    notifier, _ = _notifier(tmp_path)

    # Act
    html = notifier.build_fact_check_table(_METRIC_ROWS, _baseline_yaml(tmp_path))

    # Assert — fractions from the YAML rendered as percentages, per field.
    assert '<td class="field-col">Tax Invoice Number</td><td>99.00%</td>' in html
    assert '<td class="field-col">Buyer Name</td><td>98.00%</td>' in html


def test_build_fact_check_table_unmapped_label_falls_back_to_zero_baseline(tmp_path):
    # Arrange
    notifier, _ = _notifier(tmp_path)
    rows = [{"label": "Stamp", "accuracy": 75.0}]

    # Act — the baseline YAML has no "Stamp" key.
    html = notifier.build_fact_check_table(rows, _baseline_yaml(tmp_path))

    # Assert
    assert '<td class="field-col">Stamp</td><td>0.00%</td><td>75.00%</td>' in html


def test_build_fact_check_table_missing_baseline_file_raises(tmp_path):
    # Arrange
    notifier, _ = _notifier(tmp_path)

    # Act / Assert — surfaced to the task's best-effort catch, never a silent 0% table.
    with pytest.raises(FileNotFoundError):
        notifier.build_fact_check_table(_METRIC_ROWS, str(tmp_path / "missing_baseline.yml"))


def test_build_fact_check_table_contains_no_newlines(tmp_path):
    # Arrange
    notifier, _ = _notifier(tmp_path)

    # Act
    html = notifier.build_fact_check_table(_METRIC_ROWS, _baseline_yaml(tmp_path))

    # Assert — EmailNotifier._render converts \n to <br>, which would corrupt the markup.
    assert "\n" not in html
    assert html.startswith("<style>")
    assert html.endswith("</table>")


def test_shipped_uat_baseline_covers_every_fact_check_gt_field():
    # Arrange — the shipped config file consumed by ocr_pipeline_fact_check_post_tasks.yml.
    baseline = load_yaml(_SHIPPED_BASELINE_PATH)

    # Assert — every scored field's gt_field has a baseline entry (a miss renders as 0.00%).
    missing = [field.gt_field for field in FIELD_MAPPING if field.gt_field not in baseline]
    assert missing == []
    assert all(0.0 <= fraction <= 1.0 for fraction in baseline.values())
