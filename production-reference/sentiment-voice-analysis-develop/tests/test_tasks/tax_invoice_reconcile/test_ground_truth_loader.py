"""Tests for :class:`FactCheckSourceLoader` — schema validation, header alias rename, and I/O."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tasks.tax_invoice_reconcile.module.ground_truth_loader import FactCheckSourceLoader
from tasks.tax_invoice_reconcile.schema.ground_truth import GroundTruthSchema

_ALIAS_COLUMNS = list(GroundTruthSchema.to_schema().columns.keys())
_FIELD_NAMES = list(GroundTruthSchema.__annotations__.keys())


def _excel_bytes(df: pd.DataFrame) -> bytes:
    """Serialize a DataFrame to real .xlsx bytes (as SharePoint would return)."""
    with io.BytesIO() as buf:
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
        return buf.getvalue()


def _ground_truth_df() -> pd.DataFrame:
    """A one-row ground-truth frame keyed by the workbook's free-text alias headers."""
    row = dict.fromkeys(_ALIAS_COLUMNS, "x")
    row["File Name"] = "invoice_001.pdf"
    row["Tax Invoice Number"] = "B00110053682"
    # A trailing non-scored column (with a non-breaking space) must not break the load.
    row["Invoice\xa0Document"] = "some doc"
    return pd.DataFrame([row])


def _sp(content: bytes) -> MagicMock:
    sp = MagicMock()
    sp.get_item_by_path.return_value = MagicMock(content=content)
    return sp


class TestLoad:
    def test_renames_alias_headers_to_canonical_field_names(self):
        # Arrange
        cfg = {"ground_truth_file": "/sites/ctrl/fact_check/ground_truth.xlsx"}
        sp = _sp(_excel_bytes(_ground_truth_df()))
        loader = FactCheckSourceLoader(sp, cfg)

        # Act
        df = loader.load_ground_truth()

        # Assert
        assert list(df.columns) == _FIELD_NAMES
        assert df["tax_invoice_number"].iloc[0] == "B00110053682"

    def test_fetches_the_configured_path(self):
        # Arrange
        cfg = {"ground_truth_file": "/sites/ctrl/fact_check/ground_truth.xlsx"}
        sp = _sp(_excel_bytes(_ground_truth_df()))
        loader = FactCheckSourceLoader(sp, cfg)

        # Act
        loader.load_ground_truth()

        # Assert
        sp.get_item_by_path.assert_called_once_with(item_path="/sites/ctrl/fact_check/ground_truth.xlsx")

    def test_resolves_env_placeholder_in_path(self, monkeypatch):
        # Arrange
        monkeypatch.setenv("GT_PATH", "/sites/ctrl/fact_check/ground_truth.xlsx")
        cfg = {"ground_truth_file": "${GT_PATH}"}
        sp = _sp(_excel_bytes(_ground_truth_df()))
        loader = FactCheckSourceLoader(sp, cfg)

        # Act
        loader.load_ground_truth()

        # Assert
        sp.get_item_by_path.assert_called_once_with(item_path="/sites/ctrl/fact_check/ground_truth.xlsx")

    def test_missing_config_key_raises_key_error(self):
        # Arrange
        loader = FactCheckSourceLoader(MagicMock(), {})

        # Act / Assert
        with pytest.raises(KeyError, match="ground_truth_file"):
            loader.load_ground_truth()
