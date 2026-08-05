"""Tests for :class:`ReportSourceLoader` (tax_invoice_reconcile).

Covers: the latest-file pattern match (reverse sort over ``list_files_pattern``
results), the empty-folder ``FileNotFoundError``, and the three ``load_*`` methods
(Master-Buyer / Master-Vendor / Z45) end to end — download bytes, parse Excel, and
validate/rename onto the canonical schema field names.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tasks.tax_invoice_reconcile.module.report_source_loader import ReportSourceLoader
from tasks.tax_invoice_reconcile.schema.master_buyer import MasterBuyer
from tasks.tax_invoice_reconcile.schema.master_vendor import MasterVendor
from tasks.tax_invoice_reconcile.schema.z45_input import Z45Input

_Z45_FIELDS = list(Z45Input.to_schema().columns.keys())


def _excel_bytes(df: pd.DataFrame) -> bytes:
    """Serialize a DataFrame to real .xlsx bytes (as SharePoint would return)."""
    with io.BytesIO() as buf:
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
        return buf.getvalue()


def _sp(matches: list[str], content: bytes = b"") -> MagicMock:
    sp = MagicMock()
    sp.list_files_pattern.return_value = matches
    sp.get_item_by_path.return_value = MagicMock(content=content)
    return sp


def _master_buyer_df(tax_id: str = "0107538000012") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "No.": 1,
                "Com Code in SAP": "1000",
                "ชื่อบริษัท": "บริษัท ทดสอบ จำกัด",
                "Company Name": "Test Co., Ltd.",
                "Tax ID": tax_id,
                "ที่อยู่บริษัท": "123 ถนนทดสอบ",
                "Company Address": "123 Test Road",
            }
        ]
    )


def _master_vendor_df(vendor_code: str = "0001234") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Vendor code": vendor_code,
                "Vendor Name ( EN )": "Test Vendor",
                "Vendor Name ( TH )": "ผู้ขายทดสอบ",
            }
        ]
    )


def _z45_row(**overrides: str) -> pd.DataFrame:
    """A single positional raw Z45 row (all blank strings) with given field overrides."""
    row = dict.fromkeys(_Z45_FIELDS, "")
    row.update(overrides)
    return pd.DataFrame([[row[name] for name in _Z45_FIELDS]], columns=_Z45_FIELDS)


class TestLatestFile:
    def test_load_master_buyer_picks_latest_match_by_reverse_sort(self):
        # Arrange
        cfg = {"master_buyer_path": "/sites/x/Master", "master_buyer_file": "MasterBuyer_*.xlsx"}
        matches = ["/sites/x/Master/MasterBuyer_20260101.xlsx", "/sites/x/Master/MasterBuyer_20260301.xlsx"]
        sp = _sp(matches, _excel_bytes(_master_buyer_df()))
        loader = ReportSourceLoader(sp, cfg)

        # Act
        loader.load_master_buyer()

        # Assert — the lexicographically-latest path was fetched.
        sp.get_item_by_path.assert_called_once_with(item_path="/sites/x/Master/MasterBuyer_20260301.xlsx")

    def test_load_master_buyer_empty_folder_raises_file_not_found(self):
        # Arrange
        cfg = {"master_buyer_path": "/sites/x/Master", "master_buyer_file": "MasterBuyer_*.xlsx"}
        sp = _sp([])
        loader = ReportSourceLoader(sp, cfg)

        # Act / Assert
        with pytest.raises(FileNotFoundError, match="No master buyer file found"):
            loader.load_master_buyer()

    def test_load_master_vendor_empty_folder_raises_file_not_found(self):
        # Arrange
        cfg = {"master_vendor_path": "/sites/x/Master", "master_vendor_file": "MasterVendor_*.xlsx"}
        sp = _sp([])
        loader = ReportSourceLoader(sp, cfg)

        # Act / Assert
        with pytest.raises(FileNotFoundError, match="No master vendor file found"):
            loader.load_master_vendor()

    def test_load_z45_empty_folder_raises_file_not_found(self):
        # Arrange
        cfg = {"z45_report_path": "/sites/x/VAT", "z45_report_file": "ZAPRPT45_*.xlsx"}
        sp = _sp([])
        loader = ReportSourceLoader(sp, cfg)

        # Act / Assert
        with pytest.raises(FileNotFoundError, match="No Z45 report file found"):
            loader.load_z45()

    def test_latest_file_resolves_env_placeholder_in_folder_path(self, monkeypatch):
        # Arrange
        monkeypatch.setenv("MASTER_ROOT", "/sites/x/Master")
        cfg = {"master_buyer_path": "${MASTER_ROOT}", "master_buyer_file": "MasterBuyer_*.xlsx"}
        sp = _sp(["/sites/x/Master/MasterBuyer_20260101.xlsx"], _excel_bytes(_master_buyer_df()))
        loader = ReportSourceLoader(sp, cfg)

        # Act
        loader.load_master_buyer()

        # Assert
        sp.list_files_pattern.assert_called_once_with(folder_path="/sites/x/Master", pattern="MasterBuyer_*.xlsx")


class TestLoadMasterBuyer:
    def test_load_master_buyer_preserves_leading_zero_tax_id(self):
        # Arrange
        cfg = {"master_buyer_path": "/sites/x/Master", "master_buyer_file": "MasterBuyer_*.xlsx"}
        sp = _sp(["/sites/x/Master/MasterBuyer_20260301.xlsx"], _excel_bytes(_master_buyer_df("0107538000012")))
        loader = ReportSourceLoader(sp, cfg)

        # Act
        df = loader.load_master_buyer()

        # Assert — dtype=str on "Tax ID" keeps the leading zero.
        assert df["tax_id"].iloc[0] == "0107538000012"

    def test_load_master_buyer_renames_columns_to_canonical_field_names(self):
        # Arrange
        cfg = {"master_buyer_path": "/sites/x/Master", "master_buyer_file": "MasterBuyer_*.xlsx"}
        sp = _sp(["/sites/x/Master/MasterBuyer_20260301.xlsx"], _excel_bytes(_master_buyer_df()))
        loader = ReportSourceLoader(sp, cfg)

        # Act
        df = loader.load_master_buyer()

        # Assert
        assert list(df.columns) == list(MasterBuyer.__annotations__.keys())
        assert df["company_name_eng"].iloc[0] == "Test Co., Ltd."


class TestLoadMasterVendor:
    def test_load_master_vendor_preserves_leading_zero_vendor_code(self):
        # Arrange
        cfg = {"master_vendor_path": "/sites/x/Master", "master_vendor_file": "MasterVendor_*.xlsx"}
        sp = _sp(["/sites/x/Master/MasterVendor_20260301.xlsx"], _excel_bytes(_master_vendor_df("0001234")))
        loader = ReportSourceLoader(sp, cfg)

        # Act
        df = loader.load_master_vendor()

        # Assert
        assert df["vendor_code"].iloc[0] == "0001234"
        assert list(df.columns) == list(MasterVendor.__annotations__.keys())


class TestLoadZ45:
    def test_load_z45_adds_path_file_column_with_the_downloaded_path(self):
        # Arrange
        cfg = {"z45_report_path": "/sites/x/VAT", "z45_report_file": "ZAPRPT45_*.xlsx"}
        latest = "/sites/x/VAT/ZAPRPT45_20260301.xlsx"
        raw = _z45_row(company="1000", ref_doc_inv="INV-001")
        sp = _sp([latest], _excel_bytes(raw))
        loader = ReportSourceLoader(sp, cfg)

        # Act
        df = loader.load_z45()

        # Assert
        assert df["path_file"].iloc[0] == latest
        assert df["ref_doc_inv"].iloc[0] == "INV-001"

    def test_load_z45_picks_latest_of_multiple_matches(self):
        # Arrange
        cfg = {"z45_report_path": "/sites/x/VAT", "z45_report_file": "ZAPRPT45_*.xlsx"}
        matches = [
            "/sites/x/VAT/ZAPRPT45_20260101.xlsx",
            "/sites/x/VAT/ZAPRPT45_20260401.xlsx",
            "/sites/x/VAT/ZAPRPT45_20260201.xlsx",
        ]
        sp = _sp(matches, _excel_bytes(_z45_row(company="1000")))
        loader = ReportSourceLoader(sp, cfg)

        # Act
        df = loader.load_z45()

        # Assert
        assert df["path_file"].iloc[0] == "/sites/x/VAT/ZAPRPT45_20260401.xlsx"
