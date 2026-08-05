"""Pandera schema for the Master Buyer reference sheet."""

import pandera.pandas as pa
from pandera import Field


class MasterBuyer(pa.DataFrameModel):
    """Schema for the Master Buyer sheet, keyed by SAP company code and tax ID."""

    no: pa.typing.Series[int] = Field(alias="No.", nullable=True)
    com_code_in_sap: pa.typing.Series[str] = Field(alias="Com Code in SAP", nullable=True)
    company_name_th: pa.typing.Series[str] = Field(alias="ชื่อบริษัท", nullable=True)
    company_name_eng: pa.typing.Series[str] = Field(alias="Company Name", nullable=True)
    tax_id: pa.typing.Series[str] = Field(alias="Tax ID", nullable=True)
    company_address_th: pa.typing.Series[str] = Field(alias="ที่อยู่บริษัท", nullable=True)
    company_address_eng: pa.typing.Series[str] = Field(alias="Company Address", nullable=True)

    class Config:
        coerce = True
        strict = False
