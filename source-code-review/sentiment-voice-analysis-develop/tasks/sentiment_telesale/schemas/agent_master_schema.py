import pandas as pd
import pandera.pandas as pa
from pandera import Field


class AgentMasterSchema(pa.DataFrameModel):
    emp_id: pa.typing.Series[str] = Field(nullable=True)
    commission_skill_code: pa.typing.Series[str] = Field(nullable=True)
    commission_skill: pa.typing.Series[str] = Field(nullable=True)
    updatedate: pa.typing.Series[pa.DateTime] = Field(nullable=True)

    class Config:
        coerce = True  # Automatically convert data types when validating the DataFrame
        strict = False  # Allow extra columns — agent master Excel may contain additional fields

    @pa.dataframe_parser
    def coalesce_blank_to_null(cls, df: pd.DataFrame) -> pd.DataFrame:
        str_cols = df.select_dtypes(include="object").columns
        df[str_cols] = df[str_cols].replace(r"^\s*$", pd.NA, regex=True)
        return df
