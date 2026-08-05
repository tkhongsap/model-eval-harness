import typing
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from tasks.sentiment_telesale.output_validation.common.common import Common


class CheckList(BaseModel):
    model_config = ConfigDict(extra="ignore")

    operation_check_list: list[
        Literal["UPTOLNoProduct", "UPTOLNoSpeed", "UPTOLNoTax", "UPTOLNoProrate", "UPTOLNoGuide"]
    ] = Field(
        default_factory=list,
        description="[System Prompt: Category 5] Operation Check List for True / Dtac 10_True_UTOL campaign. Tags 5.1.1-5.1.X See system prompt section 5.1 for complete criteria.",
    )

    support_detail: str = Field(
        ...,
        # max_length=400,
        description="Evidence-based reasoning for each tag decision. Quote exact agent/customer phrases (in Thai) that justify why each tag was or was not applied. More tags = more detail required (≥3 tags: each needs full evidence paragraph). If empty list: quote key disclosure phrases proving compliance. Maximum 800 characters (~200 tokens) - Vertex AI enforces during generation.",
    )


class UtolValidation(Common):
    model_config = ConfigDict(extra="ignore")

    campaign_name: Literal["10_True_UTOL"] = Field(
        ...,
        description="Campaign name is 10_True_UTOL.",
    )

    check_list: CheckList = Field(
        ...,
        description="[System Prompt: Main Category 5] Check List - Verifies presence/absence of required disclosures and appropriate cross-sell/upsell behavior for True / Dtac 10_True_UTOL campaign. See system prompt Category 5 for complete criteria.",
    )

    @model_validator(mode="after")
    def force_campaign_name(self):
        """
        Ensure campaign_name is always '10_True_UTOL' for this validation class.
        """
        self.campaign_name = "10_True_UTOL"
        return self


def build_utol_validation(tag_codes: list) -> type:
    """
    Build a UtolValidation class with operation_check_list constrained
    to only the provided (active) tag codes via a dynamic Literal type.
    Falls back to the static UtolValidation if tag_codes is empty.
    Parameters:
        - tag_codes: List of active tag codes (e.g., ['UPTOLNoProduct', 'UPTOLNoSpeed']) to include in the operation_check_list Literal type.
    Returns:
        - A dynamically created UtolValidation class with the operation_check_list field constrained to the provided
    """
    if not tag_codes:
        return UtolValidation

    literal_type = typing.Literal.__getitem__(tuple(tag_codes))

    DynamicCheckList = create_model(  # noqa: N806
        "CheckList",
        __config__=ConfigDict(extra="ignore"),
        operation_check_list=(
            list[literal_type],
            CheckList.model_fields["operation_check_list"],
        ),
        support_detail=(str, CheckList.model_fields["support_detail"]),
    )

    return create_model(
        "UtolValidation",
        __base__=UtolValidation,
        __config__=ConfigDict(extra="ignore"),
        check_list=(DynamicCheckList, UtolValidation.model_fields["check_list"]),
    )
