"""Send templated Microsoft Graph email notifications for the tax-invoice pipeline.

Each notification is a plain-text template under a configured directory; ``{NAME}``
placeholders are filled via :meth:`str.format` and newlines are converted to ``<br>`` so
the message renders as HTML (:meth:`MSGraphModule.send_email` always sends ``HTML`` body).

:meth:`EmailNotifier.build_fact_check_table` renders the HTML fragment substituted into
the fact-check result template's ``{FACT_CHECK_TABLE}`` placeholder (same visual language
as the QA fact-check table, accuracy-only).
"""

from __future__ import annotations

from pathlib import Path

from src.modules.microsoft.msgraph import MSGraphModule
from src.utils.file_utils import load_yaml, read_file
from src.utils.logger import Logger
from tasks.tax_invoice_reconcile.helper.constant import FIELD_MAPPING, OVERALL_LABEL

logger = Logger(__name__)

# Display label (metric-row "label") -> canonical ground-truth column name (baseline YAML key).
_LABEL_TO_GT_FIELD = {field.label: field.gt_field for field in FIELD_MAPPING}


class EmailNotifier:
    """Render a ``.txt`` template to HTML and send it via Microsoft Graph."""

    def __init__(self, msgraph: MSGraphModule, template_dir: str) -> None:
        """Store the Graph transport and the template directory.

        Recipients are supplied per send (see :meth:`send_template`) so a single notifier can
        route different notifications to different from/to/cc addresses.

        Args:
            msgraph: Authenticated Microsoft Graph transport.
            template_dir: Directory holding the ``.txt`` notification templates.
        """
        self._msgraph = msgraph
        self._template_dir = Path(template_dir)

    def send_template(
        self,
        template_name: str,
        subject: str,
        *,
        sender_email: str,
        receiver_email: str | list[str],
        cc_email: str | list[str] | None = None,
        **placeholders: object,
    ) -> None:
        """Render ``template_name`` with ``placeholders`` and send it as an HTML email.

        Args:
            template_name: File name of the template under ``template_dir`` (e.g. ``report.txt``).
            subject: Email subject line.
            sender_email: Licensed sender mailbox (UPN).
            receiver_email: Primary recipient(s) — comma-separated string or list.
            cc_email: CC recipient(s) — comma-separated string, list, or ``None``.
            **placeholders: Values substituted into the template's ``{NAME}`` placeholders.

        Raises:
            FileNotFoundError: If the template file does not exist.
            requests.HTTPError: If the Graph send fails after all retries.
        """
        body = self._render(template_name, placeholders)
        self._msgraph.send_email(
            subject=subject,
            body=body,
            sender_email=sender_email,
            receiver_email=receiver_email,
            cc_email=cc_email,
        )
        logger.info(f"Sent notification '{template_name}' (subject: {subject!r}).")

    def build_fact_check_table(self, metric_rows: list[dict], baseline_path: str) -> str:
        """Render fact-check metric rows as a single-line HTML table (Field | Baseline | Accuracy).

        Only ``accuracy`` is shown — with ``FN = TN = 0`` it is the only metric that carries
        information (see ``FactCheckEvaluator``). The ``overall`` row is excluded (it has no
        UAT baseline); it still reaches the AI-Operation fact-check log.

        Args:
            metric_rows: ``FactCheckEvaluator.evaluate()`` rows (``label`` + metric percentages).
            baseline_path: YAML file mapping ``gt_field`` names (``FIELD_MAPPING`` spelling,
                e.g. ``tax_invoice_number``) to UAT baseline fractions (``0.99`` -> ``99.00%``);
                a label without a mapped baseline entry shows 0.00%.

        Returns:
            A ``<style>`` + ``<table>`` HTML fragment with no newlines, safe to substitute into
            a template rendered by :meth:`send_template` (whose newline -> ``<br>`` conversion
            would corrupt multi-line markup).

        Raises:
            FileNotFoundError: If the baseline YAML file does not exist.
        """
        baseline_config = load_yaml(baseline_path)
        baseline_percent = {key: value * 100 for key, value in baseline_config.items()}
        style = (
            "<style>"
            ".fc-table { border-collapse: collapse; font-family: Arial; font-size: 11px; border: 1px solid #444; } "
            ".fc-table th { background-color: #0078d4; color: white; border: 1px solid #ffffff; padding: 4px 8px; "
            "text-align: center; } "
            ".fc-table td { border: 1px solid #ccc; padding: 4px 6px; text-align: center; } "
            ".fc-table .field-col { text-align: left !important; font-weight: bold; min-width: 100px; }"
            "</style>"
        )
        header = '<table class="fc-table"><thead><tr><th>Field</th><th>Baseline</th><th>Accuracy</th></tr></thead>'
        rows = "".join(
            self._build_fact_check_row(row, baseline_percent) for row in metric_rows if row["label"] != OVERALL_LABEL
        )
        return f"{style}{header}<tbody>{rows}</tbody></table>"

    def _render(self, template_name: str, placeholders: dict[str, object]) -> str:
        """Read a template, fill placeholders (when any), and convert newlines to ``<br>``."""
        text = read_file(self._template_dir / template_name)
        if placeholders:
            text = text.format(**placeholders)
        return text.replace("\n", "<br>\n")

    @staticmethod
    def _build_fact_check_row(row: dict, baseline_percent: dict[str, float]) -> str:
        """Return one ``<tr>`` for a metric row: label, UAT baseline (via ``gt_field``), accuracy."""
        gt_field = _LABEL_TO_GT_FIELD.get(row["label"], "")
        return (
            "<tr>"
            f'<td class="field-col">{row["label"]}</td>'
            f"<td>{baseline_percent.get(gt_field, 0.00):.2f}%</td>"
            f"<td>{row['accuracy']:.2f}%</td>"
            "</tr>"
        )
