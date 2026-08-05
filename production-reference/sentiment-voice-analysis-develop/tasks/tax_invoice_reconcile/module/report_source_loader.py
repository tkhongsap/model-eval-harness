"""Load the Master-Buyer and SAP ZAPRPT45 (Z45) source files from SharePoint.

Both files are picked as the latest match of a filename pattern (reverse sort), read
from the returned bytes, and validated against their schema. The Master-Buyer "Tax ID"
column is read as text so Excel's numeric inference can't drop the leading zero; the
Z45 export is renamed by position via :func:`validate_z45` because its headers are
unreliable.
"""

from __future__ import annotations

import io

import pandas as pd
import pandera.pandas as pa

from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.common import resolve_env
from src.utils.logger import Logger
from tasks.tax_invoice_reconcile.schema.master_buyer import MasterBuyer
from tasks.tax_invoice_reconcile.schema.master_vendor import MasterVendor
from tasks.tax_invoice_reconcile.schema.z45_input import validate_z45

logger = Logger(__name__)


class ReportSourceLoader:
    """Fetch and validate the Master-Buyer and Z45 reports from a SharePoint site."""

    def __init__(self, sp_source: SharePointModule, source_site_cfg: dict) -> None:
        """Initialise with an injected SharePoint module and the ``source_site`` config.

        Args:
            sp_source: SharePoint module for the source document site.
            source_site_cfg: The task's ``sharepoint.source_site`` config block (holds
                ``master_buyer_path``/``master_buyer_file`` and
                ``z45_report_path``/``z45_report_file``).
        """
        self._sp = sp_source
        self._cfg = source_site_cfg

    @staticmethod
    def canonical_validate(df: pd.DataFrame, schema: type[pa.DataFrameModel]) -> pd.DataFrame:
        """Validate *df* against *schema*'s aliased columns, renamed to canonical field names."""
        alias_keys = list(schema.to_schema().columns.keys())
        validated = schema.validate(df[alias_keys])
        validated.columns = list(schema.__annotations__.keys())
        return validated

    def _latest_file(self, folder_key: str, pattern_key: str, label: str) -> str:
        """Return the latest SharePoint file path matching the configured pattern.

        Args:
            folder_key: Config key holding the folder path to search.
            pattern_key: Config key holding the filename pattern to match.
            label: Human-readable label for log/error messages.

        Returns:
            The most recent matching file path (reverse-sorted).

        Raises:
            FileNotFoundError: If no file in the folder matches the pattern.
        """
        folder_path = resolve_env(self._cfg.get(folder_key))
        pattern = self._cfg.get(pattern_key)
        matches = self._sp.list_files_pattern(folder_path=folder_path, pattern=pattern)
        latest = max(matches, default=None)
        if not latest:
            logger.error(f"No {label} file found in SharePoint at {folder_path} ({pattern})")
            raise FileNotFoundError(f"No {label} file found in SharePoint.")
        return latest

    def _load_master(
        self, folder_key: str, pattern_key: str, label: str, schema: type[pa.DataFrameModel], key_col: str
    ) -> pd.DataFrame:
        """Load the latest master workbook and return it with canonical field names."""
        path = self._latest_file(folder_key, pattern_key, label)
        res = self._sp.get_item_by_path(item_path=path)
        with io.BytesIO(res.content) as stream:
            # Read the key column as text so Excel's numeric inference can't drop the
            # leading zero (or inject a ``.0``); the OCR side is a 13-char string.
            df = pd.read_excel(stream, engine="openpyxl", dtype={key_col: str})
        return self.canonical_validate(df, schema)

    def load_master_buyer(self) -> pd.DataFrame:
        """Load the latest Master-Buyer file and return it with canonical field names."""
        return self._load_master("master_buyer_path", "master_buyer_file", "master buyer", MasterBuyer, "Tax ID")

    def load_master_vendor(self) -> pd.DataFrame:
        """Load the latest Master-Vendor file and return it with canonical field names."""
        return self._load_master(
            "master_vendor_path", "master_vendor_file", "master vendor", MasterVendor, "Vendor code"
        )

    def load_z45(self) -> pd.DataFrame:
        """Load the latest Z45 report, validated/typed via positional renaming."""
        path = self._latest_file("z45_report_path", "z45_report_file", "Z45 report")
        res = self._sp.get_item_by_path(item_path=path)
        with io.BytesIO(res.content) as stream:
            raw = pd.read_excel(stream, engine="openpyxl", dtype=str)
        # The SAP ZAPRPT45 export carries unreliable headers (non-breaking spaces,
        # truncation, drift, duplicates), so validate_z45 renames columns by position
        # against Z45Input's field order and validates typed amounts/dates.
        df = validate_z45(raw)
        df["path_file"] = path
        return df
