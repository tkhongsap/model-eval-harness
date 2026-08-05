from pydantic import BaseModel, ConfigDict, Field


class PositiveCustomerExperience(BaseModel):
    model_config = ConfigDict(extra="ignore")

    failure_to_demonstrate_empathy: bool = Field(
        ...,
        description="[System Prompt: 3.1.1] failure_to_demonstrate_empathy - Demonstrates Empathy: Shows understanding and compassion for customer's service issues. See system prompt section 3.1.1 for complete evaluation criteria.",
    )

    deflecting_responsibility: bool = Field(
        ...,
        description="[System Prompt: 3.1.2] deflecting_responsibility - Takes Responsibility: Accepts complaints clearly and commits to resolution. See system prompt section 3.1.2 for complete evaluation criteria.",
    )

    escalates_customer_emotion: bool = Field(
        ...,
        description="[System Prompt: 3.1.3] tone_escalates_emotion - De-escalates Customer Emotion: Maintains calm tone that reduces customer frustration. See system prompt section 3.1.3 for complete evaluation criteria.",
    )

    support_detail: str = Field(
        ...,
        # max_length=400,
        description="Quote exact agent phrases. For False: cite the moment lacking empathy or deflecting + explain what criteria was not met. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.",
    )


class ClarityOfCommunication(BaseModel):
    model_config = ConfigDict(extra="ignore")

    overly_technical_language: bool = Field(
        ...,
        description="[System Prompt: 3.2.1] uses_overly_technical_language - Uses Simple Language: Communicates in plain language avoiding jargon. See system prompt section 3.2.1 for complete evaluation criteria.",
    )

    fails_to_clarify_limitations: bool = Field(
        ...,
        description="[System Prompt: 3.2.2] fails_to_clarify_misunderstandings - Clarifies Common Misunderstandings: Proactively explains limitations and conditions. See system prompt section 3.2.2 for complete evaluation criteria.",
    )

    no_adjustment_for_complexity: bool = Field(
        ...,
        description="[System Prompt: 3.2.3] does_not_adjust_explanation_style - Adjusts Explanation When Needed: Adapts explanation method when customer shows confusion. See system prompt section 3.2.3 for complete evaluation criteria.",
    )

    support_detail: str = Field(
        ...,
        # max_length=400,
        description="Quote exact agent phrases. For False: cite the jargon/unclear language used + what simpler alternative should have been said. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.",
    )


class BuildingTrust(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provides_unclear_information: bool = Field(
        ...,
        description="[System Prompt: 3.3.1] provides_unclear_information - Provides Clear, On-Point Information: Answers directly to questions asked. See system prompt section 3.3.1 for complete evaluation criteria.",
    )

    provides_misleading_information: bool = Field(
        ...,
        description="[System Prompt: 3.3.2] provides_misleading_information - Provides Accurate Information: Information matches system facts and policies. See system prompt section 3.3.2 for complete evaluation criteria.",
    )

    fails_to_connect_value: bool = Field(
        ...,
        description="[System Prompt: 3.3.3] fails_to_connect_price_to_value - Connects Price to Value: Links pricing and features to specific customer benefits. See system prompt section 3.3.3 for complete evaluation criteria.",
    )

    support_detail: str = Field(
        ...,
        # max_length=400,
        description="Quote exact agent phrases. For False: cite the unclear/misleading statement + what accurate information should have been provided. More False = more detail per finding. Maximum 400 characters (~100 tokens) - Vertex AI enforces during generation.",
    )


class CustomerExperience(BaseModel):
    model_config = ConfigDict(extra="ignore")

    positive_customer_experience: PositiveCustomerExperience = Field(
        ...,
        description="[System Prompt: 3.1] Positive Customer Experience: Agent creates positive feelings through empathy and de-escalation. See system prompt section 3.1 for complete criteria.",
    )

    clarity_of_communication: ClarityOfCommunication = Field(
        ...,
        description="[System Prompt: 3.2] Clarity of Communication: Agent communicates clearly and adapts to customer needs. See system prompt section 3.2 for complete criteria.",
    )

    building_trust: BuildingTrust = Field(
        ...,
        description="[System Prompt: 3.3] Building Trust and Customer Value: Agent builds trust through accurate information. See system prompt section 3.3 for complete criteria.",
    )
