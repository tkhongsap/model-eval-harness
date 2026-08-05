import pandas as pd
import pandera.pandas as pa
from pandera import Field


class ControlLogSchema(pa.DataFrameModel):
    run_dt: pa.typing.Series[str] = Field(nullable=True)
    datamonth: pa.typing.Series[str] = Field(nullable=True)
    datadate: pa.typing.Series[str] = Field(nullable=True)
    processed_status: pa.typing.Series[str] = Field(nullable=True)
    remark: pa.typing.Series[str] = Field(nullable=True)

    class Config:
        coerce = True  # Automatically convert data types when validating the DataFrame
        strict = False  # Allow extra columns — ControlLog sheet may contain additional fields

    @pa.dataframe_parser
    def coalesce_blank_to_null(cls, df: pd.DataFrame) -> pd.DataFrame:
        str_cols = df.select_dtypes(include="object").columns
        df[str_cols] = df[str_cols].replace(r"^\s*$", pd.NA, regex=True)
        return df
