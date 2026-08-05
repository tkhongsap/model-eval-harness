from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CustomerInsight(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rejection_reason: (
        Literal["Price Sensitivity", "Package Mismatch", "Existing Plan Satisfaction", "Decision Deferral"] | None
    ) = Field(
        None,
        description="[System Prompt: Additional Category 2.1] Rejection Reason - Identifies primary reason for customer rejecting offer. See system prompt Additional Category 2.1 for complete criteria.",
    )

    network_issue: Literal["Signal", "Speed", "Coverage"] | None = Field(
        None,
        description="[System Prompt: Additional Category 2.2] Network Issue - Identifies specific network issue cited by customer. See system prompt Additional Category 2.2 for complete criteria.",
    )

    churn_risk_indicator: int = Field(
        default=0,
        description="[System Prompt: Additional Category 2.3] Churn Risk - Percentage likelihood (0-100) that customer will churn based on conversation insights. See system prompt Additional Category 2.3 for evaluation guidelines.",
    )

    customer_sentiment_emotional: Literal["Positive", "Neutral", "Negative"] = Field(
        description="[System Prompt: Additional Category 2.4] Customer Sentiment - Assesses the overall sentiment of the customer throughout the call based on their statements and tone. See system prompt Additional Category 2.4 for complete criteria."
    )

    @model_validator(mode="after")
    def validate_churn_risk_indicator(self):
        """
        Validate that churn_risk_indicator is between 0 and 100.
        """
        if self.churn_risk_indicator < 0 or self.churn_risk_indicator > 100:
            raise ValueError("Churn Risk Indicator must be percentage between 0 and 100.")
        return self
