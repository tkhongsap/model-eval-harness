"""Tests for the Suspicious-page reject (SourceRejecter) and the header-only VAT workbook.

SourceRejecter resolves each Suspicious row's immutable GCS ``child_path`` via the
pre-processing-log + page-manifest join and copies that exact page into the reject folder;
OutputExporter writes a header-only VAT workbook when nothing maps.
"""

from __future__ import annotations

from unittest.mock import Mock

import pandas as pd

from tasks.tax_invoice_reconcile.module.output_exporter import OutputExporter
from tasks.tax_invoice_reconcile.module.source_rejecter import SourceRejecter

_ETAX = "/AI TAX Invoice/Input_TAX Invoices/E-TAX/0001_pornpa8/inv.pdf"


def _frames():
    final_df = pd.DataFrame({"FILE_PATH": [_ETAX], "PAGE_NO": [2], "STATUS": ["SUSPICIOUS"]})
    pre_log = pd.DataFrame(
        {"sharepoint_input_path": [_ETAX], "gcs_landing_path": ["gs://bkt/landing/inv.pdf"], "job_id": ["J1"]}
    )
    manifest = pd.DataFrame(
        {
            "parent_path": ["gs://bkt/landing/inv.pdf"],
            "job_id": ["J1"],
            "page_no": [2],
            "child_path": ["gs://bkt/proc/inv_p002.pdf"],
        }
    )
    return final_df, pre_log, manifest


def test_resolve_targets_maps_suspicious_row_to_its_child_path():
    final_df, pre_log, manifest = _frames()
    assert SourceRejecter._resolve_targets(final_df, pre_log, manifest) == [(_ETAX, "gs://bkt/proc/inv_p002.pdf")]


def test_resolve_targets_ignores_non_suspicious_rows():
    final_df, pre_log, manifest = _frames()
    final_df["STATUS"] = ["SUCCESS"]
    assert SourceRejecter._resolve_targets(final_df, pre_log, manifest) == []


def test_reject_suspicious_downloads_exact_gcs_page_and_uploads_to_reject():
    final_df, pre_log, manifest = _frames()
    gcs = Mock()
    gcs.download_file_from_gcs.return_value = b"page-bytes"
    sp = Mock()
    rejecter = SourceRejecter(sp, lambda _bucket: gcs, "/root/Archive_Reject")

    rejecter.reject_suspicious(final_df, pre_log, manifest, 20260605)

    gcs.download_file_from_gcs.assert_called_once_with("gs://bkt/proc/inv_p002.pdf")
    dest, content = sp.upload_file.call_args[0]
    assert dest == "/root/Archive_Reject/20260605/0001_pornpa8/inv_p002.pdf"
    assert content == b"page-bytes"


def test_reject_suspicious_noop_when_reject_root_blank():
    final_df, pre_log, manifest = _frames()
    sp = Mock()
    SourceRejecter(sp, lambda _b: Mock(), "").reject_suspicious(final_df, pre_log, manifest, 20260605)
    sp.upload_file.assert_not_called()


def test_export_vat_writes_header_only_when_z45_is_empty():
    # ref is None (empty Z45) → a header-only VAT workbook must still be written.
    sp = Mock()
    exporter = OutputExporter(sp, "/dest")
    z45_empty = pd.DataFrame(columns=["Company", "Ref. Doc  (Inv.)", "Vat Amount"])
    grp = pd.DataFrame({"File Name": ["inv.pdf"], "Invoice Number": ["INV-1"]})

    exporter._export_vat(grp, z45_empty, None, "/VAT Report_Paper [Scan]/20260605/x_Output_Z45.xlsx", None)

    sp.upload_file.assert_called_once()
    _dest, content = sp.upload_file.call_args[0]
    assert isinstance(content, (bytes, bytearray)) and len(content) > 0  # a real (header-only) workbook
