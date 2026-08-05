"""Tests for :class:`SourceArchiver` (tax_invoice_reconcile) — copy + delete-on-success.

Covers: each distinct source invoice is downloaded and re-uploaded to its E-TAX / Paper +
flat ``DATADATE`` archive path with the original filename; the Z45 report is archived to its
dated folder; the original is **deleted from the source site only after a successful upload**;
an upload failure skips the delete; and a delete failure is swallowed. SharePoint is mocked at
the boundary.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from tasks.tax_invoice_reconcile.module.source_archiver import SourceArchiver

_DATADATE = 20260617
_ARC_INV = "Sites/AI TAX Invoice/Archive_TAX Invoice"
_ARC_VAT = "Sites/AI TAX Invoice/Archive_VAT Report"
_PAPER = "/sites/x/Input_TAX Invoices/Paper [Scan]/1068_20260508_PINICHAKORN_1.pdf"
_ETAX = "/sites/x/Input_TAX Invoices/E-TAX/SOMCHAI/scan 1.jpg"
_Z45 = "/sites/x/Input_VAT Report/ZAPRPT45_20260508.xlsx"


def _processing(file_paths: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"FILE_PATH": file_paths})


def _src(content: bytes = b"RAWBYTES") -> MagicMock:
    sp = MagicMock()
    sp.get_item_by_path.return_value = MagicMock(content=content)
    return sp


def _uploads(sp: MagicMock) -> list[tuple]:
    return [call.args for call in sp.upload_file.call_args_list]


def _deleted(sp: MagicMock) -> list[str]:
    return [call.args[0] for call in sp.delete_item.call_args_list]


def test_archive_invoices_copies_each_source_to_dated_archive_path():
    # Arrange
    src, dest = _src(b"PDF"), MagicMock()
    archiver = SourceArchiver(src, dest, _ARC_INV, _ARC_VAT)

    # Act
    archiver.archive_invoices(_processing([_PAPER, _ETAX]), _DATADATE)

    # Assert — original filenames preserved; routed by type + flat DATADATE folder.
    assert _uploads(dest) == [
        (f"{_ARC_INV}/E-TAX/20260617/scan 1.jpg", b"PDF"),
        (f"{_ARC_INV}/Paper [Scan]/20260617/1068_20260508_PINICHAKORN_1.pdf", b"PDF"),
    ]


def test_archive_invoices_deletes_each_source_after_successful_upload():
    # Arrange
    src, dest = _src(b"PDF"), MagicMock()
    archiver = SourceArchiver(src, dest, _ARC_INV, _ARC_VAT)

    # Act
    archiver.archive_invoices(_processing([_PAPER, _ETAX]), _DATADATE)

    # Assert — both originals deleted from the source site by their source path.
    assert set(_deleted(src)) == {_PAPER, _ETAX}


def test_archive_skips_delete_when_upload_fails():
    # Arrange
    src, dest = _src(b"PDF"), MagicMock()
    dest.upload_file.side_effect = Exception("upload boom")
    archiver = SourceArchiver(src, dest, _ARC_INV, _ARC_VAT)

    # Act
    archiver.archive_invoices(_processing([_PAPER]), _DATADATE)

    # Assert — an un-archived original is never removed.
    src.delete_item.assert_not_called()


def test_source_delete_failure_is_swallowed():
    # Arrange
    src, dest = _src(b"PDF"), MagicMock()
    src.delete_item.side_effect = Exception("locked")
    archiver = SourceArchiver(src, dest, _ARC_INV, _ARC_VAT)

    # Act — must not raise even though delete fails.
    archiver.archive_invoices(_processing([_PAPER]), _DATADATE)

    # Assert — the archive copy still happened; delete was attempted.
    assert _uploads(dest) == [(f"{_ARC_INV}/Paper [Scan]/20260617/1068_20260508_PINICHAKORN_1.pdf", b"PDF")]
    src.delete_item.assert_called_once_with(_PAPER)


def test_archive_z45_copies_source_then_deletes_it():
    # Arrange
    src, dest = _src(b"XLSX"), MagicMock()
    archiver = SourceArchiver(src, dest, _ARC_INV, _ARC_VAT)

    # Act
    archiver.archive_z45(_Z45, _DATADATE)

    # Assert
    assert _uploads(dest) == [(f"{_ARC_VAT}/20260617/ZAPRPT45_20260508.xlsx", b"XLSX")]
    src.delete_item.assert_called_once_with(_Z45)


def test_archive_z45_blank_path_is_noop():
    # Arrange
    src, dest = _src(), MagicMock()
    archiver = SourceArchiver(src, dest, _ARC_INV, _ARC_VAT)

    # Act
    archiver.archive_z45("", _DATADATE)

    # Assert
    dest.upload_file.assert_not_called()
    src.delete_item.assert_not_called()
