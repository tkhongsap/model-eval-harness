"""Pandera schema and positional loader for the SAP ZAPRPT45 (Input-VAT report).

The raw export carries duplicate, truncated, non-breaking-space, and Thai column
headers (e.g. two ``Tax Cleari…`` columns holding a clearing document number and a
clearing date, or ``Ref.\xa0Doc\xa0\xa0(Inv.)`` with NBSPs). Header strings are
therefore unreliable as keys, so :func:`validate_z45` renames columns **by
position** against the field order of :class:`Z45Input` rather than by matching
header text. The schema is keyed by field name (no ``alias``); the field
definition order below is the contract and must match the physical column order of
the export.
"""

from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
from pandera import Field
from pandera.engines import pandas_engine

# Money columns carry exact ``Decimal`` values (THB satang precision), kept in sync
# with the OCR-side money fields.
_MONEY_KW = {"precision": 18, "scale": 2}

# SAP renders dates as ``dd.mm.yyyy`` and amounts with thousands separators and an
# optional trailing minus (e.g. ``"1,234.56-"`` for a credit).
_DATE_FORMAT = "%d.%m.%Y"
_AMOUNT_COLUMNS = ("vat_amount", "tax_base_amount", "net_paid")
_DATE_COLUMNS = ("payment_date", "send_date", "process_date", "tax_clearing_date")


class Z45Input(pa.DataFrameModel):
    """Typed contract for the ZAPRPT45 report (one row per payment/clearing line).

    Field order mirrors the SAP export column order; :func:`validate_z45` relies on
    it for positional renaming. String fields are nullable because SAP leaves cells
    blank; amounts are exact ``Decimal``; dates are parsed from ``dd.mm.yyyy``.
    """

    company: pa.typing.Series[str] = Field(nullable=True)
    ref_doc_inv: pa.typing.Series[str] = Field(nullable=True)
    doc_type: pa.typing.Series[str] = Field(nullable=True)
    invoice_document: pa.typing.Series[str] = Field(nullable=True)
    vendor_code: pa.typing.Series[str] = Field(nullable=True)
    vendor_name: pa.typing.Series[str] = Field(nullable=True)
    vat_amount: pa.typing.Series[pandas_engine.Decimal] = Field(nullable=True, dtype_kwargs=_MONEY_KW)
    tax_base_amount: pa.typing.Series[pandas_engine.Decimal] = Field(nullable=True, dtype_kwargs=_MONEY_KW)
    payment_document: pa.typing.Series[str] = Field(nullable=True)
    payment_method: pa.typing.Series[str] = Field(nullable=True)
    short_text: pa.typing.Series[str] = Field(nullable=True)
    encashment: pa.typing.Series[str] = Field(nullable=True)
    payment_date: pa.typing.Series[pandas_engine.DateTime] = Field(nullable=True)
    cheque_no: pa.typing.Series[str] = Field(nullable=True)
    payee_name: pa.typing.Series[str] = Field(nullable=True)
    doc_header_text: pa.typing.Series[str] = Field(nullable=True)
    document_currency: pa.typing.Series[str] = Field(nullable=True)
    net_paid: pa.typing.Series[pandas_engine.Decimal] = Field(nullable=True, dtype_kwargs=_MONEY_KW)
    cost_center: pa.typing.Series[str] = Field(nullable=True)
    tax_code: pa.typing.Series[str] = Field(nullable=True)
    tax_clearing_doc: pa.typing.Series[str] = Field(nullable=True)
    tax_clearing_date: pa.typing.Series[pandas_engine.DateTime] = Field(nullable=True)
    tax_id: pa.typing.Series[str] = Field(nullable=True)
    branch_code: pa.typing.Series[str] = Field(nullable=True)
    email_requester: pa.typing.Series[str] = Field(nullable=True)
    tax_invoice_number: pa.typing.Series[str] = Field(nullable=True)
    check_duplicate: pa.typing.Series[str] = Field(nullable=True)
    send_date: pa.typing.Series[pandas_engine.DateTime] = Field(nullable=True)
    aging: pa.typing.Series[str] = Field(nullable=True)
    pending_for_release: pa.typing.Series[str] = Field(nullable=True)
    vat_status: pa.typing.Series[str] = Field(nullable=True)
    clearing_doc: pa.typing.Series[str] = Field(nullable=True)
    process_date: pa.typing.Series[pandas_engine.DateTime] = Field(nullable=True)
    remark: pa.typing.Series[str] = Field(nullable=True)
    user: pa.typing.Series[str] = Field(nullable=True)
    user_outward: pa.typing.Series[str] = Field(nullable=True)
    department: pa.typing.Series[str] = Field(nullable=True)

    class Config:
        coerce = True
        strict = False

    @pa.dataframe_parser
    def _normalize(cls, df: pd.DataFrame) -> pd.DataFrame:  # noqa: N805  (pandera passes the model class)
        """Normalize SAP text/amount/date cells before coercion.

        Runs before ``coerce`` (unlike ``@pa.parser``, which runs after): blanks
        become ``pd.NA``, amount strings are stripped of thousands separators and a
        trailing minus (moved to the front), and dates are parsed from either the SAP
        ``dd.mm.yyyy`` text form or the ISO datetime string an Excel date cell yields
        under ``dtype=str``. Coercion then casts the cleaned values to
        ``Decimal``/``datetime64``.
        """
        str_cols = df.select_dtypes(include="object").columns
        # Opt into pandas' future (non-downcasting) ``replace`` behavior locally so
        # blank->NA keeps these columns as object without the deprecation warning;
        # scoped to this call to avoid a global side effect.
        with pd.option_context("future.no_silent_downcasting", True):
            df[str_cols] = df[str_cols].replace(r"^\s*$", pd.NA, regex=True)
        for col in _AMOUNT_COLUMNS:
            if col in df.columns:
                s = df[col].astype("string").str.replace(",", "", regex=False).str.strip()
                neg = s.str.endswith("-").fillna(False)
                s = s.str.rstrip("-")
                df[col] = s.mask(neg, "-" + s)
        for col in _DATE_COLUMNS:
            if col in df.columns:
                # SAP text exports use dd.mm.yyyy; real Excel *date* cells arrive (under
                # the loader's dtype=str) as ISO 'YYYY-MM-DD HH:MM:SS'. Parse the SAP
                # format first (a general parse would misread 05.03.2026 as month-first),
                # then fall back to a general parse for the ISO datetime strings the SAP
                # format leaves as NaT.
                sap = pd.to_datetime(df[col], format=_DATE_FORMAT, errors="coerce")
                iso = pd.to_datetime(df[col], errors="coerce")
                df[col] = sap.fillna(iso)
        return df


def validate_z45(df: pd.DataFrame) -> pd.DataFrame:
    """Rename the raw ZAPRPT45 export by position and validate it.

    The export's header strings are unreliable (NBSPs, truncation, duplicates), so
    columns are relabelled positionally to :class:`Z45Input`'s field names before
    validation rather than matched by text.

    Args:
        df: Raw export frame (read with ``dtype=str``); its column count must equal
            the number of ``Z45Input`` fields, in the same physical order.

    Returns:
        The validated frame with canonical snake_case field-name columns and typed
        amounts/dates.

    Raises:
        ValueError: If the raw frame's column count does not match the schema.
    """
    field_names = list(Z45Input.to_schema().columns.keys())
    if len(df.columns) != len(field_names):
        raise ValueError(f"column count mismatch: expected {len(field_names)}, got {len(df.columns)}")
    df = df.copy()
    df.columns = field_names
    return Z45Input.validate(df)
