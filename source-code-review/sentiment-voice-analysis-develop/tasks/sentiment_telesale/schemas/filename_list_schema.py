import pandas as pd
import pandera.pandas as pa
from pandera import Field


class FilenameListSchema(pa.DataFrameModel):
    filename: pa.typing.Series[str] = Field(nullable=False)
    commission_skill_code: pa.typing.Series[str] = Field(nullable=True)
    commission_skill: pa.typing.Series[str] = Field(nullable=True)

    class Config:
        coerce = True  # Automatically convert data types when validating the DataFrame
        strict = True  # Raise an error if there are any unexpected columns in the DataFrame

    @pa.dataframe_parser
    def coalesce_blank_to_null(cls, df: pd.DataFrame) -> pd.DataFrame:
        str_cols = df.select_dtypes(include="object").columns
        df[str_cols] = df[str_cols].replace(r"^\s*$", pd.NA, regex=True)
        return df
