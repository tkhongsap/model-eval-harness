"""Tests for OutputExporter — link-based VAT slicing + per-document routing.

The VAT workbook is filled from the reconciliation engine's Z45↔document match link
(``z45_link_df``: ``_z_id``/``file_name``), NOT by invoice-number equality — a document that
reconciles without line-item refs (scen 2/4/5, report "Invoice Number" = ``'No'``) must still
carry its mapped Z45 rows.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pandas as pd

from tasks.tax_invoice_reconcile.helper.constant import MappingZ45Status
from tasks.tax_invoice_reconcile.module.output_exporter import OutputExporter
from tasks.tax_invoice_reconcile.schema.report_output import ReportOutput
from tasks.tax_invoice_reconcile.schema.z45_output import Z45Output

_STATUS_IDX = list(Z45Output.__annotations__).index("mapping_tax_invoice_status")
_REF_IDX = list(Z45Output.__annotations__).index("ref_doc_inv")
_N_COLS = len(Z45Output.__annotations__)
_REPORT_ALIASES = list(ReportOutput.to_schema().columns.keys())

_ETAX_PATH = "/AI TAX Invoice/Input_TAX Invoices/E-TAX/0001_pornpa8/inv1.pdf"
_PAPER_PATH = "/AI TAX Invoice/Input_TAX Invoices/Paper [Scan]/inv2.pdf"


def _z45_frame_full(overrides: list[dict[str, str]]) -> pd.DataFrame:
    """Build a positional Z45-shaped frame; each dict maps field name -> cell value."""
    field_index = list(Z45Output.__annotations__)
    rows = []
    for o in overrides:
        row = [""] * _N_COLS
        for field, value in o.items():
            row[field_index.index(field)] = value
        rows.append(row)
    return pd.DataFrame(rows)


def _link(pairs: list[tuple[int, str]]) -> pd.DataFrame:
    """Build a z45_link_df from (_z_id, file_name) pairs."""
    return pd.DataFrame({"_z_id": [p[0] for p in pairs], "file_name": [p[1] for p in pairs]})


def _report_row(file_name: str, tax_invoice_number: str, invoice_number: str) -> dict:
    """A single Output-Report row (all aliases blank except the ones given)."""
    row = dict.fromkeys(_REPORT_ALIASES, "")
    row["File Name"] = file_name
    row["Tax Invoice Number"] = tax_invoice_number
    row["Invoice Number"] = invoice_number
    return row


class TestLinkedRowIds:
    """Tests for OutputExporter._linked_row_ids — the group's Z45 row-position selection."""

    def test_returns_sorted_unique_positions_for_the_groups_files(self):
        grp = pd.DataFrame({"File Name": ["a.pdf", "a.pdf"]})
        link = _link([(3, "a.pdf"), (1, "a.pdf"), (3, "a.pdf"), (2, "b.pdf")])

        assert OutputExporter._linked_row_ids(grp, link, 5) == [1, 3]

    def test_none_link_returns_empty(self):
        grp = pd.DataFrame({"File Name": ["a.pdf"]})
        assert OutputExporter._linked_row_ids(grp, None, 5) == []

    def test_empty_link_returns_empty(self):
        grp = pd.DataFrame({"File Name": ["a.pdf"]})
        assert OutputExporter._linked_row_ids(grp, _link([]), 5) == []

    def test_out_of_range_ids_are_dropped(self):
        grp = pd.DataFrame({"File Name": ["a.pdf"]})
        link = _link([(0, "a.pdf"), (9, "a.pdf")])

        assert OutputExporter._linked_row_ids(grp, link, 1) == [0]

    def test_zero_rows_returns_empty(self):
        grp = pd.DataFrame({"File Name": ["a.pdf"]})
        assert OutputExporter._linked_row_ids(grp, _link([(0, "a.pdf")]), 0) == []


class TestZ45SortKeys:
    """Tests for OutputExporter._z45_sort_keys — the stripped vendor_name/ref_doc_inv lookup."""

    def test_returns_stripped_vendor_and_ref_columns(self):
        z45 = _z45_frame_full(
            [
                {"vendor_name": "  ACME CO  ", "ref_doc_inv": "  INV-001  "},
                {"vendor_name": "ZETA CO", "ref_doc_inv": "INV-002"},
            ]
        )

        result = OutputExporter._z45_sort_keys(z45)

        assert list(result["vendor_name"]) == ["ACME CO", "ZETA CO"]
        assert list(result["ref_doc_inv"]) == ["INV-001", "INV-002"]

    def test_none_input_returns_none(self):
        assert OutputExporter._z45_sort_keys(None) is None

    def test_empty_input_returns_none(self):
        assert OutputExporter._z45_sort_keys(pd.DataFrame()) is None


class TestUpload:
    """Tests for OutputExporter._upload — serialize + upload, swallowing SharePoint failures."""

    def test_upload_failure_is_logged_and_swallowed(self):
        sp = MagicMock()
        sp.upload_file.side_effect = Exception("SharePoint locked")
        exporter = OutputExporter(sp, "/dest")
        df = pd.DataFrame({"A": [1, 2]})

        # Act — must not raise even though the upload fails.
        exporter._upload(df, "/dest/some.xlsx", "Sheet1", "Extract&Mapping")

        # Assert
        sp.upload_file.assert_called_once()


class TestExport:
    """End-to-end tests for OutputExporter.export — routing + per-document upload."""

    @staticmethod
    def _processing_df() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "FILE_NAME": ["inv1.pdf", "inv2.pdf"],
                "FILE_PATH": [_ETAX_PATH, _PAPER_PATH],
                "DATADATE": [20260605, 20260605],
            }
        )

    def test_export_noop_when_report_df_is_none(self):
        sp = MagicMock()
        exporter = OutputExporter(sp, "/dest")

        exporter.export(self._processing_df(), None, pd.DataFrame())

        sp.upload_file.assert_not_called()

    def test_export_noop_when_report_df_is_empty(self):
        sp = MagicMock()
        exporter = OutputExporter(sp, "/dest")

        exporter.export(self._processing_df(), pd.DataFrame(), pd.DataFrame())

        sp.upload_file.assert_not_called()

    def test_export_routes_and_uploads_mapping_and_vat_workbooks_per_document(self):
        # Arrange — inv1 is E-TAX (linked to a Completed Z45 row), inv2 is Paper [Scan]
        # with no link entry (nothing mapped -> header-only).
        report_df = pd.DataFrame([_report_row("inv1.pdf", "INV1", "PAY-100"), _report_row("inv2.pdf", "INV2", "No")])
        z45_enriched_df = _z45_frame_full(
            [
                {"ref_doc_inv": "PAY-100", "mapping_tax_invoice_status": MappingZ45Status.COMPLETED.value},
                {"ref_doc_inv": "OTHER", "mapping_tax_invoice_status": ""},
            ]
        )
        sp = MagicMock()
        exporter = OutputExporter(sp, "/dest")

        # Act
        exporter.export(self._processing_df(), report_df, z45_enriched_df, _link([(0, "inv1.pdf")]))

        # Assert — one Extract&Mapping + one VAT workbook per routed document.
        assert sp.upload_file.call_count == 4
        paths = {call.args[0] for call in sp.upload_file.call_args_list}
        assert paths == {
            "/dest/Extract&Mapping_E-TAX/20260605/pornpa8_20260605_0001_Output_ETAX.xlsx",
            "/dest/Extract&Mapping_Paper [Scan]/20260605/inv2_Output.xlsx",
            "/dest/VAT Report_E-TAX/20260605/pornpa8_20260605_0001_Output_Z45_ETAX.xlsx",
            "/dest/VAT Report_Paper [Scan]/20260605/inv2_Output_Z45.xlsx",
        }

    def test_export_matched_vat_workbook_contains_only_the_linked_rows(self):
        # Arrange
        report_df = pd.DataFrame([_report_row("inv1.pdf", "INV1", "PAY-100"), _report_row("inv2.pdf", "INV2", "No")])
        z45_enriched_df = _z45_frame_full(
            [
                {"ref_doc_inv": "PAY-100", "mapping_tax_invoice_status": MappingZ45Status.COMPLETED.value},
                {"ref_doc_inv": "OTHER", "mapping_tax_invoice_status": ""},
            ]
        )
        sp = MagicMock()
        exporter = OutputExporter(sp, "/dest")

        # Act
        exporter.export(self._processing_df(), report_df, z45_enriched_df, _link([(0, "inv1.pdf")]))

        # Assert — the linked VAT workbook has exactly 1 data row; the unlinked one is header-only.
        content_by_path = {call.args[0]: call.args[1] for call in sp.upload_file.call_args_list}
        matched = content_by_path["/dest/VAT Report_E-TAX/20260605/pornpa8_20260605_0001_Output_Z45_ETAX.xlsx"]
        header_only = content_by_path["/dest/VAT Report_Paper [Scan]/20260605/inv2_Output_Z45.xlsx"]
        assert len(pd.read_excel(io.BytesIO(matched))) == 1
        assert len(pd.read_excel(io.BytesIO(header_only))) == 0

    def test_export_vat_includes_linked_rows_for_document_without_invoice_numbers(self):
        # Arrange — the AMARIN/TRUE INTERNET regression: the document reconciled at header
        # level (report "Invoice Number" is the 'No' sentinel), so attribution must come from
        # the engine link, not from invoice-number equality.
        report_df = pd.DataFrame([_report_row("inv2.pdf", "INV2", "No")])
        z45_enriched_df = _z45_frame_full(
            [
                {
                    "vendor_name": "ZETA CO",
                    "ref_doc_inv": "Z-REF-A",
                    "mapping_tax_invoice_status": MappingZ45Status.COMPLETED.value,
                },
                {
                    "vendor_name": "ACME CO",
                    "ref_doc_inv": "Z-REF-B",
                    "mapping_tax_invoice_status": MappingZ45Status.INCOMPLETED.value,
                },
                {"vendor_name": "OTHER CO", "ref_doc_inv": "UNRELATED", "mapping_tax_invoice_status": ""},
            ]
        )
        sp = MagicMock()
        exporter = OutputExporter(sp, "/dest")

        # Act
        exporter.export(self._processing_df(), report_df, z45_enriched_df, _link([(0, "inv2.pdf"), (1, "inv2.pdf")]))

        # Assert — both linked rows are in the workbook, ordered by vendor name before
        # ref_doc_inv (ACME/Z-REF-B ahead of ZETA/Z-REF-A); the unlinked row is not.
        content_by_path = {call.args[0]: call.args[1] for call in sp.upload_file.call_args_list}
        vat = pd.read_excel(io.BytesIO(content_by_path["/dest/VAT Report_Paper [Scan]/20260605/inv2_Output_Z45.xlsx"]))
        assert len(vat) == 2
        assert list(vat.iloc[:, _REF_IDX]) == ["Z-REF-B", "Z-REF-A"]

    def test_export_vat_orders_by_ref_doc_inv_within_the_same_vendor(self):
        # Arrange — three linked rows of one vendor arrive in shuffled ref order.
        report_df = pd.DataFrame([_report_row("inv2.pdf", "INV2", "No")])
        z45_enriched_df = _z45_frame_full(
            [
                {"vendor_name": "ACME CO", "ref_doc_inv": "REF-3", "mapping_tax_invoice_status": "Completed"},
                {"vendor_name": "ACME CO", "ref_doc_inv": "REF-1", "mapping_tax_invoice_status": "Completed"},
                {"vendor_name": "ACME CO", "ref_doc_inv": "REF-2", "mapping_tax_invoice_status": "Completed"},
            ]
        )
        sp = MagicMock()
        exporter = OutputExporter(sp, "/dest")

        # Act
        exporter.export(
            self._processing_df(),
            report_df,
            z45_enriched_df,
            _link([(0, "inv2.pdf"), (1, "inv2.pdf"), (2, "inv2.pdf")]),
        )

        # Assert
        content_by_path = {call.args[0]: call.args[1] for call in sp.upload_file.call_args_list}
        vat = pd.read_excel(io.BytesIO(content_by_path["/dest/VAT Report_Paper [Scan]/20260605/inv2_Output_Z45.xlsx"]))
        assert list(vat.iloc[:, _REF_IDX]) == ["REF-1", "REF-2", "REF-3"]

    def test_export_vat_excludes_rows_linked_only_to_other_documents(self):
        # Arrange — a Z45 line mapped by inv1 must not leak into inv2's workbook.
        report_df = pd.DataFrame([_report_row("inv1.pdf", "INV1", "No"), _report_row("inv2.pdf", "INV2", "No")])
        z45_enriched_df = _z45_frame_full(
            [{"ref_doc_inv": "Z-REF-1", "mapping_tax_invoice_status": MappingZ45Status.COMPLETED.value}]
        )
        sp = MagicMock()
        exporter = OutputExporter(sp, "/dest")

        # Act
        exporter.export(self._processing_df(), report_df, z45_enriched_df, _link([(0, "inv1.pdf")]))

        # Assert
        content_by_path = {call.args[0]: call.args[1] for call in sp.upload_file.call_args_list}
        inv1_vat = pd.read_excel(
            io.BytesIO(content_by_path["/dest/VAT Report_E-TAX/20260605/pornpa8_20260605_0001_Output_Z45_ETAX.xlsx"])
        )
        inv2_vat = pd.read_excel(
            io.BytesIO(content_by_path["/dest/VAT Report_Paper [Scan]/20260605/inv2_Output_Z45.xlsx"])
        )
        assert len(inv1_vat) == 1
        assert len(inv2_vat) == 0

    def test_export_skips_rows_with_no_resolvable_output_path(self):
        # Arrange — "ghost.pdf" has no matching row in processing_df, so the routing
        # left-join leaves OUTPUT_PATH null; the row must be skipped, not raise.
        processing_df = pd.DataFrame({"FILE_NAME": ["other.pdf"], "FILE_PATH": [_PAPER_PATH], "DATADATE": [20260605]})
        report_df = pd.DataFrame([_report_row("ghost.pdf", "INV3", "No")])
        sp = MagicMock()
        exporter = OutputExporter(sp, "/dest")

        # Act — must not raise.
        exporter.export(processing_df, report_df, pd.DataFrame())

        # Assert
        sp.upload_file.assert_not_called()
