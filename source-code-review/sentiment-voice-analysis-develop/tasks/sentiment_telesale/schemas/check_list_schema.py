import pandas as pd
import pandera.pandas as pa
from pandera import Field


class CheckListCategorySchema(pa.DataFrameModel):
    commission_skill_code: pa.typing.Series[int] = Field(nullable=False)
    commission_skill: pa.typing.Series[str] = Field(nullable=False)
    main_category_no: pa.typing.Series[int] = Field(nullable=True)
    main_category_name: pa.typing.Series[str] = Field(nullable=True)
    section: pa.typing.Series[str] = Field(nullable=True)
    order: pa.typing.Series[int] = Field(nullable=True)
    content: pa.typing.Series[str] = Field(nullable=True)

    class Config:
        coerce = True  # Automatically convert data types when validating the DataFrame
        strict = True  # Raise an error if there are any unexpected columns in the DataFrame

    @pa.dataframe_parser
    def coalesce_blank_to_null(cls, df: pd.DataFrame) -> pd.DataFrame:
        str_cols = df.select_dtypes(include="object").columns
        df[str_cols] = df[str_cols].replace(r"^\s*$", pd.NA, regex=True)
        return df


class CheckListSchemaSubcategoriesSchema(pa.DataFrameModel):
    commission_skill_code: pa.typing.Series[int] = Field(nullable=False)
    sub_category_no: pa.typing.Series[str] = Field(nullable=True)
    sub_category_name: pa.typing.Series[str] = Field(nullable=True)
    order: pa.typing.Series[int] = Field(nullable=True)
    section: pa.typing.Series[str] = Field(nullable=True)
    content: pa.typing.Series[str] = Field(nullable=True)

    class Config:
        coerce = True  # Automatically convert data types when validating the DataFrame
        strict = True  # Raise an error if there are any unexpected columns in the DataFrame

    @pa.dataframe_parser
    def coalesce_blank_to_null(cls, df: pd.DataFrame) -> pd.DataFrame:
        str_cols = df.select_dtypes(include="object").columns
        df[str_cols] = df[str_cols].replace(r"^\s*$", pd.NA, regex=True)
        return df


class CheckListTagsSchema(pa.DataFrameModel):
    commission_skill_code: pa.typing.Series[int] = Field(nullable=False)
    item_no: pa.typing.Series[str] = Field(nullable=True)
    tag_code: pa.typing.Series[str] = Field(nullable=True)
    rule_and_logic: pa.typing.Series[str] = Field(nullable=True)
    positive_example_th: pa.typing.Series[str] = Field(nullable=True)
    negative_example_th: pa.typing.Series[str] = Field(nullable=True)
    is_active: pa.typing.Series[bool] = Field(nullable=True)

    class Config:
        coerce = True  # Automatically convert data types when validating the DataFrame
        strict = True  # Raise an error if there are any unexpected columns in the DataFrame

    @pa.dataframe_parser
    def coalesce_blank_to_null(cls, df: pd.DataFrame) -> pd.DataFrame:
        str_cols = df.select_dtypes(include="object").columns
        df[str_cols] = df[str_cols].replace(r"^\s*$", pd.NA, regex=True)
        # Convert is_active strings to bool here, before coerce=True runs.
        # @pa.parser runs AFTER coercion in pandera 0.30+, so any non-empty string
        # (including "False") would be cast to True by astype(bool) before a parser
        # could fix it. @pa.dataframe_parser runs BEFORE coercion, so this is safe.
        if "is_active" in df.columns:
            _true_values = {"1", "t", "y", "true"}
            _false_values = {"0", "f", "n", "false"}

            def _convert(val):
                if pd.isna(val):
                    return val
                if isinstance(val, bool):
                    return val
                normalized = str(val).strip().lower()
                if normalized in _true_values:
                    return True
                if normalized in _false_values:
                    return False
                return val

            df["is_active"] = df["is_active"].map(_convert)
        return df
