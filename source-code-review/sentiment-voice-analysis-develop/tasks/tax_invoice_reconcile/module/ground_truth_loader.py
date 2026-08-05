"""Load and validate the fact-check ground-truth workbook from the control SharePoint site.

The ground truth lives at a single configured path on the control site (not a dated pattern), so
it is fetched directly by path, read as text (so tax ids / amounts keep their exact printed form),
validated against :class:`GroundTruthSchema`, and returned with canonical ``snake_case`` column
names — the same alias-rename dance :class:`ReportSourceLoader` uses for the Master Buyer.
"""

from __future__ import annotations

import io

import pandas as pd

from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.common import resolve_env
from src.utils.logger import Logger
from tasks.tax_invoice_reconcile.module.report_source_loader import ReportSourceLoader
from tasks.tax_invoice_reconcile.schema.ground_truth import GroundTruthSchema
from tasks.tax_invoice_reconcile.schema.master_buyer import MasterBuyer

logger = Logger(__name__)


class FactCheckSourceLoader:
    """Fetch and validate the ground-truth and master buyer workbooks from a SharePoint site."""

    def __init__(self, sp_control: SharePointModule, control_site_cfg: dict) -> None:
        """Initialise with an injected SharePoint module and the ``control_site`` config block.

        Args:
            sp_control: SharePoint module for the control document site.
            control_site_cfg: The task's ``sharepoint.control_site`` config block; must hold
                ``ground_truth_file`` and ``master_buyer_path`` (a fully resolved item path, possibly with ``${ENV}``).
        """
        self._sp = sp_control
        self._cfg = control_site_cfg

    def load_ground_truth(self) -> pd.DataFrame:
        """Load the ground-truth workbook and return it with canonical field names.

        Returns:
            The validated frame with ``GroundTruthSchema`` field names as columns.

        Raises:
            KeyError: If ``ground_truth_file`` is absent from the config block.
        """
        key = "ground_truth_file"
        path = resolve_env(self._cfg.get(key))
        if not path:
            raise KeyError(f"Missing '{key}' in control_site config.")
        res = self._sp.get_item_by_path(item_path=path)
        with io.BytesIO(res.content) as stream:
            df = pd.read_excel(stream, engine="openpyxl", dtype=str)
        validated = ReportSourceLoader.canonical_validate(df, GroundTruthSchema)
        logger.info(f"Loaded ground truth: {len(validated)} row(s) from {path}")
        return validated

    def load_master_buyer(self) -> pd.DataFrame:
        """Load the master buyer workbook and return it with canonical field names.

        Returns:
            The validated frame with ``MasterBuyer`` field names as columns.

        Raises:
            KeyError: If ``master_buyer_path`` is absent from the config block.
        """
        key = "master_buyer_path"
        path = resolve_env(self._cfg.get(key))
        if not path:
            raise KeyError(f"Missing '{key}' in control_site config.")
        res = self._sp.get_item_by_path(item_path=path)
        with io.BytesIO(res.content) as stream:
            df = pd.read_excel(stream, engine="openpyxl", dtype={"Tax ID": str})
        return ReportSourceLoader.canonical_validate(df, MasterBuyer)
