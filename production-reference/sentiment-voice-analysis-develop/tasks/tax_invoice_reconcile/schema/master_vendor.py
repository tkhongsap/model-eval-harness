"""Pandera schema for the Master Vendor reference sheet."""

import pandera.pandas as pa
from pandera import Field


class MasterVendor(pa.DataFrameModel):
    """Schema for the Master Vendor sheet, used to test vendor-name similarity."""

    vendor_code: pa.typing.Series[str] = Field(nullable=True, alias="Vendor code")
    vendor_name_eng: pa.typing.Series[str] = Field(nullable=True, alias="Vendor Name ( EN )")
    vendor_name_th: pa.typing.Series[str] = Field(nullable=True, alias="Vendor Name ( TH )")

    class Config:
        coerce = True
        strict = False
