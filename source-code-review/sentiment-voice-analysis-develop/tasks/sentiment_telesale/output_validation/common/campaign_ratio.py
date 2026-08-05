from pydantic import BaseModel, ConfigDict, Field, model_validator


class CampaignRatio(BaseModel):
    model_config = ConfigDict(extra="ignore")

    main: float = Field(
        ...,
        # ge=0.0,
        # le=1.0,
        description="[System Prompt: Campaign Ratio] Percentage (0.0-1.0) of conversation discussing the main campaign referenced in campaign_name. Represents time/content spent on primary campaign offer. Must sum with 'other' to equal 1.0. Example: 0.75 means 75% of conversation is about main campaign.",
    )

    other: float = Field(
        ...,
        # ge=0.0,
        # le=1.0,
        description="[System Prompt: Campaign Ratio] Percentage (0.0-1.0) of conversation discussing topics NOT related to main campaign (billing, technical support, other promotions, general inquiries). Must sum with 'main' to equal 1.0. Example: 0.25 means 25% of conversation is about other topics.",
    )

    @model_validator(mode="after")
    def decimal_limit(self) -> "CampaignRatio":
        self.main = round(self.main, 2)
        self.other = round(self.other, 2)
        return self
