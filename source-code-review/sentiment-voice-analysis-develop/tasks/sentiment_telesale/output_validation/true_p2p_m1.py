import typing
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from tasks.sentiment_telesale.output_validation.common.common import Common


class CheckList(BaseModel):
    model_config = ConfigDict(extra="ignore")

    operation_check_list: list[
        Literal[
            "P2PNoProduct",
            "P2PNoFairusage",
            "P2PNoPrice",
            "P2PNoDatainfo",
            "P2PNoVoiceinfo",
            "P2PNoprivilege",
            "P2PNoSetbudget",
            "P2PNoBillcycle",
            "P2PNoBillpayment",
            "P2PNoBilldelivery",
        ]
    ] = Field(
        default_factory=list,
        description="[System Prompt: Category 5] Operation Check List for True / Dtac 08_True_P2P_M1 campaign. Tags 5.1.1-5.1.X See system prompt section 5.1 for complete criteria.",
    )

    support_detail: str = Field(
        ...,
        # max_length=400,
        description="Evidence-based reasoning for each tag decision. Quote exact agent/customer phrases (in Thai) that justify why each tag was or was not applied. More tags = more detail required (≥3 tags: each needs full evidence paragraph). If empty list: quote key disclosure phrases proving compliance. Maximum 800 characters (~200 tokens) - Vertex AI enforces during generation.",
    )


class P2PM1Validation(Common):
    model_config = ConfigDict(extra="ignore")

    campaign_name: Literal["08_True_P2P_M1"] = Field(
        ...,
        description="Campaign name is 08_True_P2P_M1.",
    )

    check_list: CheckList = Field(
        ...,
        description="[System Prompt: Main Category 5] Check List - Verifies presence/absence of required disclosures and appropriate cross-sell/upsell behavior for True / Dtac 08_True_P2P_M1 campaign. See system prompt Category 5 for complete criteria.",
    )

    @model_validator(mode="after")
    def force_campaign_name(self):
        """
        Ensure campaign_name is always '08_True_P2P_M1' for this validation class.
        """
        self.campaign_name = "08_True_P2P_M1"
        return self


def build_p2p_m1_validation(tag_codes: list) -> type:
    """
    Build a P2PM1Validation class with operation_check_list constrained
    to only the provided (active) tag codes via a dynamic Literal type.
    Falls back to the static P2PM1Validation if tag_codes is empty.
    Parameters:
        - tag_codes: List of active tag codes (e.g., ['P2PNoProduct', 'P2PNoFairusage']) to include in the operation_check_list Literal type.
    Returns:
        - A dynamically created P2PM1Validation class with the operation_check_list field constrained to the provided
    """
    if not tag_codes:
        return P2PM1Validation

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
        "P2PM1Validation",
        __base__=P2PM1Validation,
        __config__=ConfigDict(extra="ignore"),
        check_list=(DynamicCheckList, P2PM1Validation.model_fields["check_list"]),
    )
