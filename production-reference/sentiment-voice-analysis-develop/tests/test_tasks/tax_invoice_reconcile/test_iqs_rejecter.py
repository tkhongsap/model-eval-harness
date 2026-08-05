"""Tests for IqsRejecter — IQS reject-move handler in the tax-invoice reconcile package.

Covers: whole-file REJECTED move (copy then delete), copy-fail → skip delete,
PARTIAL bad-page split + upload (source left intact), and the manifest→source join
(``parent_path`` / ``gcs_landing_path``).
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pandas as pd

from tasks.tax_invoice_reconcile.helper import output_layout as ol
from tasks.tax_invoice_reconcile.module.iqs_rejecter import IqsRejecter

_ETAX = "/AI TAX Invoice/Input_TAX Invoices/E-TAX/0001_pornpa8/inv.pdf"
_PAPER = "/AI TAX Invoice/Input_TAX Invoices/Paper [Scan]/inv.pdf"
_ROOT = "/root/Archive_Reject"
_GCS_LANDING = "gs://bkt/landing/inv.pdf"


def _pre_df(sp_path: str, status: str, gcs_landing: str) -> pd.DataFrame:
    return pd.DataFrame({"sharepoint_input_path": [sp_path], "status": [status], "gcs_landing_path": [gcs_landing]})


def _manifest_df(parent_path: str, page_no: int, quality_status: str) -> pd.DataFrame:
    return pd.DataFrame({"parent_path": [parent_path], "page_no": [page_no], "quality_status": [quality_status]})


# ---------------------------------------------------------------------------
# Whole-file REJECTED
# ---------------------------------------------------------------------------


def test_rejected_file_copies_then_deletes_source():
    sp = Mock()
    sp.copy_file.return_value = True
    pre_df = _pre_df(_PAPER, "REJECTED", _GCS_LANDING)

    IqsRejecter(sp, _ROOT).reject(pre_df, pd.DataFrame(), 20260605)

    sp.copy_file.assert_called_once()
    assert sp.copy_file.call_args[0][0] == _PAPER
    sp.delete_item.assert_called_once_with(_PAPER)


def test_rejected_file_skips_delete_when_copy_fails():
    sp = Mock()
    sp.copy_file.return_value = False
    pre_df = _pre_df(_PAPER, "REJECTED", _GCS_LANDING)

    IqsRejecter(sp, _ROOT).reject(pre_df, pd.DataFrame(), 20260605)

    sp.delete_item.assert_not_called()


def test_rejected_file_dest_uses_output_layout():
    sp = Mock()
    sp.copy_file.return_value = True
    pre_df = _pre_df(_ETAX, "REJECTED", _GCS_LANDING)

    IqsRejecter(sp, _ROOT).reject(pre_df, pd.DataFrame(), 20260605)

    dest = sp.copy_file.call_args[0][1]
    assert dest == ol.reject_dest(_ROOT, _ETAX, 20260605)


def test_unsupported_rejected_row_with_empty_landing_path_moves_whole_file_and_skips_partial_merge():
    # An unsupported-extension file is logged REJECTED with an empty gcs_landing_path
    # (never uploaded to landing); manifest rows exist only for an unrelated file.
    sp = Mock()
    sp.copy_file.return_value = True
    unsupported_path = "/AI TAX Invoice/Input_TAX Invoices/Paper [Scan]/notes.webp"
    pre_df = _pre_df(unsupported_path, "REJECTED", "")
    manifest = _manifest_df(_GCS_LANDING, 1, "REJECTED")

    IqsRejecter(sp, _ROOT).reject(pre_df, manifest, 20260605)  # must not raise

    sp.copy_file.assert_called_once_with(unsupported_path, ol.reject_dest(_ROOT, unsupported_path, 20260605))
    sp.delete_item.assert_called_once_with(unsupported_path)
    sp.upload_file.assert_not_called()  # no page-split upload for the whole-file reject


# ---------------------------------------------------------------------------
# PARTIAL bad-page split
# ---------------------------------------------------------------------------


def test_partial_page_uploaded_without_deleting_source():
    sp = Mock()
    sp.get_item_by_path.return_value.content = b"fake-pdf"
    pre_df = _pre_df(_ETAX, "PARTIAL", _GCS_LANDING)
    manifest = _manifest_df(_GCS_LANDING, 1, "REJECTED")

    with patch("tasks.tax_invoice_reconcile.module.iqs_rejecter.extract_single_page") as mock_ex:
        mock_ex.return_value = b"page-bytes"
        IqsRejecter(sp, _ROOT).reject(pre_df, manifest, 20260605)

    sp.upload_file.assert_called_once()
    sp.delete_item.assert_not_called()


def test_partial_page_dest_follows_reject_dest_layout():
    sp = Mock()
    sp.get_item_by_path.return_value.content = b"fake-pdf"
    pre_df = _pre_df(_ETAX, "PARTIAL", _GCS_LANDING)
    manifest = _manifest_df(_GCS_LANDING, 2, "REJECTED")

    with patch("tasks.tax_invoice_reconcile.module.iqs_rejecter.extract_single_page") as mock_ex:
        mock_ex.return_value = b"page-bytes"
        IqsRejecter(sp, _ROOT).reject(pre_df, manifest, 20260605)

    dest = sp.upload_file.call_args[0][0]
    expected = ol.reject_dest(_ROOT, _ETAX, 20260605, name="inv_p002.pdf")
    assert dest == expected


def test_partial_source_downloaded_once_for_multiple_bad_pages():
    sp = Mock()
    sp.get_item_by_path.return_value.content = b"fake-pdf"
    pre_df = _pre_df(_ETAX, "PARTIAL", _GCS_LANDING)
    manifest = pd.DataFrame(
        {
            "parent_path": [_GCS_LANDING, _GCS_LANDING],
            "page_no": [1, 3],
            "quality_status": ["REJECTED", "REJECTED"],
        }
    )

    with patch("tasks.tax_invoice_reconcile.module.iqs_rejecter.extract_single_page") as mock_ex:
        mock_ex.return_value = b"page-bytes"
        IqsRejecter(sp, _ROOT).reject(pre_df, manifest, 20260605)

    sp.get_item_by_path.assert_called_once_with(item_path=_ETAX)
    assert sp.upload_file.call_count == 2


# ---------------------------------------------------------------------------
# Manifest → source join
# ---------------------------------------------------------------------------


def test_resolve_partial_pages_joins_on_gcs_landing_path():
    partial_df = pd.DataFrame(
        {
            "status": ["PARTIAL"],
            "gcs_landing_path": [_GCS_LANDING],
            "sharepoint_input_path": [_ETAX],
        }
    )
    manifest = pd.DataFrame({"parent_path": [_GCS_LANDING], "page_no": [3], "quality_status": ["REJECTED"]})
    result = IqsRejecter._resolve_partial_pages(partial_df, manifest)
    assert list(result["sharepoint_input_path"]) == [_ETAX]
    assert list(result["page_no"]) == [3]


def test_resolve_partial_pages_excludes_accepted_pages():
    partial_df = pd.DataFrame(
        {
            "status": ["PARTIAL"],
            "gcs_landing_path": [_GCS_LANDING],
            "sharepoint_input_path": [_ETAX],
        }
    )
    manifest = pd.DataFrame(
        {
            "parent_path": [_GCS_LANDING, _GCS_LANDING],
            "page_no": [1, 2],
            "quality_status": ["ACCEPTED", "REJECTED"],
        }
    )
    result = IqsRejecter._resolve_partial_pages(partial_df, manifest)
    assert list(result["page_no"]) == [2]


def test_reject_dest_etax_preserves_company_user_subfolder():
    dest = ol.reject_dest(_ROOT, _ETAX, 20260605, name="inv_p001.pdf")
    assert dest == "/root/Archive_Reject/20260605/0001_pornpa8/inv_p001.pdf"


def test_reject_dest_paper_sits_directly_under_date():
    assert ol.reject_dest(_ROOT, _PAPER, 20260605) == "/root/Archive_Reject/20260605/inv.pdf"


# ---------------------------------------------------------------------------
# Empty-frame guard
# ---------------------------------------------------------------------------


def test_reject_noop_when_pre_run_df_is_empty():
    sp = Mock()

    IqsRejecter(sp, _ROOT).reject(pd.DataFrame(), pd.DataFrame(), 20260605)

    sp.copy_file.assert_not_called()
    sp.upload_file.assert_not_called()


# ---------------------------------------------------------------------------
# Whole-file REJECTED — exception swallow
# ---------------------------------------------------------------------------


def test_reject_whole_files_swallows_copy_file_exception():
    sp = Mock()
    sp.copy_file.side_effect = Exception("network down")
    pre_df = _pre_df(_PAPER, "REJECTED", _GCS_LANDING)

    IqsRejecter(sp, _ROOT).reject(pre_df, pd.DataFrame(), 20260605)  # must not raise

    sp.delete_item.assert_not_called()


def test_move_file_swallows_delete_item_exception():
    sp = Mock()
    sp.copy_file.return_value = True
    sp.delete_item.side_effect = Exception("locked")
    pre_df = _pre_df(_PAPER, "REJECTED", _GCS_LANDING)

    IqsRejecter(sp, _ROOT).reject(pre_df, pd.DataFrame(), 20260605)  # must not raise


# ---------------------------------------------------------------------------
# PARTIAL bad-page split — empty-frame guards + exception swallow
# ---------------------------------------------------------------------------


def test_reject_partial_pages_noop_when_manifest_empty():
    sp = Mock()
    pre_df = _pre_df(_ETAX, "PARTIAL", _GCS_LANDING)

    IqsRejecter(sp, _ROOT).reject(pre_df, pd.DataFrame(), 20260605)

    sp.get_item_by_path.assert_not_called()


def test_reject_partial_pages_noop_when_no_partial_rows():
    sp = Mock()
    pre_df = _pre_df(_ETAX, "PENDING", _GCS_LANDING)
    manifest = _manifest_df(_GCS_LANDING, 1, "REJECTED")

    IqsRejecter(sp, _ROOT).reject(pre_df, manifest, 20260605)

    sp.get_item_by_path.assert_not_called()


def test_reject_partial_pages_swallows_upload_page_splits_exception():
    sp = Mock()
    sp.get_item_by_path.side_effect = Exception("download failed")
    pre_df = _pre_df(_ETAX, "PARTIAL", _GCS_LANDING)
    manifest = _manifest_df(_GCS_LANDING, 1, "REJECTED")

    IqsRejecter(sp, _ROOT).reject(pre_df, manifest, 20260605)  # must not raise


def test_upload_page_splits_swallows_upload_file_exception():
    sp = Mock()
    sp.get_item_by_path.return_value.content = b"fake-pdf"
    sp.upload_file.side_effect = Exception("quota exceeded")
    pre_df = _pre_df(_ETAX, "PARTIAL", _GCS_LANDING)
    manifest = _manifest_df(_GCS_LANDING, 1, "REJECTED")

    with patch("tasks.tax_invoice_reconcile.module.iqs_rejecter.extract_single_page") as mock_ex:
        mock_ex.return_value = b"page-bytes"
        IqsRejecter(sp, _ROOT).reject(pre_df, manifest, 20260605)  # must not raise

    sp.upload_file.assert_called_once()
