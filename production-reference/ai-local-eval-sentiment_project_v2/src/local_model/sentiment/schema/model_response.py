"""Structured-output contract for the call-analysis Gemini prompt.

google-genai accepts a pydantic class for ``response_schema`` directly. Two things are
load-bearing: **field declaration order** (Gemini generates in ``properties`` insertion
order -- do not alphabetize or regroup) and **property names** (prompt text; the ``&``
names are preserved verbatim via ``Field(alias=...)``). Never attach
``Field(description=...)`` to a model-typed field -- a bare ``$ref`` property is replaced
wholesale during schema conversion and the sibling description is silently dropped.
"""

from __future__ import annotations

from typing import Any, Literal

from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

REASON_DESCRIPTION = "ระบุเหตุผลในการประเมิน evaluation แบบกะทัดรัดสั้นๆ ห้ามสรุปเป็น step และขอเป็นภาษาไทย"

STANDARD_GSD_NAME_DESCRIPTION = "เลือก 1 หัวข้อที่ตรงกับบทสนทนาจากรายการที่กำหนดให้ หากไม่อยู่ในหมวดหมู่ที่มี ให้ระบุชื่อหัวข้อใหม่ภาษาอังกฤษที่เหมาะสมที่สุด"


def _drop_docstring_description(schema: dict[str, Any]) -> None:
    """Keep class docstrings out of the schema handed to the model.

    Pydantic promotes a model's docstring to the object's ``description``; only
    ``Field(description=...)`` is prompt text.
    """
    schema.pop("description", None)


class _SchemaModel(BaseModel):
    """Base for every response model: alias-transparent in and out.

    ``serialize_by_alias`` means a plain ``model_dump()`` already emits the
    ``&`` property names, so no caller can forget ``by_alias=True``.

    ``extra="forbid"`` is deliberately *not* set — it would emit
    ``additionalProperties``, which the Gemini Developer API rejects.
    """

    model_config = ConfigDict(
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
        json_schema_extra=_drop_docstring_description,
    )


class CriterionEvaluation(_SchemaModel):
    """Assessment of one service-quality criterion that may be inapplicable."""

    evaluation: Literal["Meet", "Below", "N/A"]
    reason: str = Field(description=REASON_DESCRIPTION)


class BinaryCriterionEvaluation(_SchemaModel):
    """Assessment of a criterion that always applies, so there is no ``N/A``."""

    evaluation: Literal["Meet", "Below"]
    reason: str = Field(description=REASON_DESCRIPTION)


class CustomerInsight(_SchemaModel):
    """What the call reveals about the customer and their churn risk."""

    summary_story: str = Field(description="บทสรุปการสนทนา สรุปเป็นภาษาไทย")
    product_category: str
    repeat_call: Literal["Repeat Call", "New Call"]
    fcr: bool
    churn_probability: str
    churn_reason: Literal[
        "Price and Package",
        "Network and Quality",
        "Service and Experience",
        "Device Contract and Policy",
        "Competitor",
        "Other",
    ]
    customer_insight_summary: str
    standard_gsd_name: str = Field(description=STANDARD_GSD_NAME_DESCRIPTION)


class ServiceQuality(_SchemaModel):
    """Per-criterion scoring of the agent's handling of the call."""

    greeting_standard: CriterionEvaluation
    manners: BinaryCriterionEvaluation
    enthusiasm: BinaryCriterionEvaluation
    communication_skill: BinaryCriterionEvaluation
    ending_standard: CriterionEvaluation
    data_privacy: CriterionEvaluation
    legal_verification: CriterionEvaluation
    customer_verification: CriterionEvaluation
    sla_notification: CriterionEvaluation
    transfer_standard: CriterionEvaluation
    problem_understanding: BinaryCriterionEvaluation
    compensation: CriterionEvaluation
    hold_standard: CriterionEvaluation
    wrap_up: CriterionEvaluation
    beyond_scope_support: CriterionEvaluation
    true_application: CriterionEvaluation
    case_ownership: CriterionEvaluation
    contact_confirm: CriterionEvaluation
    omotenashi: CriterionEvaluation
    retention: CriterionEvaluation
    downsell: CriterionEvaluation
    mnp: CriterionEvaluation
    upselling: CriterionEvaluation
    service_quality_performance_insight: str = Field(description="สรุปเป็นภาษาไทย")


class SaleOpportunity(_SchemaModel):
    """Whether a sales opening existed and how the agent acted on it."""

    opportunity_recognition_in_conversation: bool
    product_suggested_by_ai: str
    agent_offer_product_presentation_and_explanation: bool = Field(
        alias="agent_offer_product_presentation_&_explanation"
    )
    product_offer_by_agent: str
    sales_outcome_and_customer_decision: bool = Field(alias="sales_outcome_&_customer_decision")
    sales_opportunities_performance_insight: str


class CustomerSentiment(_SchemaModel):
    """How the customer felt across the call, and what drove it."""

    overall_sentiment: Literal["Positive", "Neutral", "Negative"]
    initial_sentiment: Literal["Positive", "Neutral", "Negative"]
    final_sentiment: Literal["Positive", "Neutral", "Negative"]
    primary_sentiment_driver: Literal[
        "Issues Resolution",
        "Agent Behavior and Communication",
        "Process / Policy",
        "Price / Charges / Commercials",
        "System / IVR / Channel Experience",
        "Product / Service Feature",
    ]
    csat: str = Field(description="1 to 5")
    cs_performance_insight: str


class CustomerExperience(_SchemaModel):
    """Effort-side view: agent interaction plus the systems around it.

    The rating fields are strings ("0 to 5"), while ``system_accessibility`` and
    ``ivr_usability_&_design`` are booleans — that asymmetry is intentional and
    matches the original schema.
    """

    agent_communication_and_attitude: str = Field(
        alias="agent_communication_&_attitude", description="0 to 5"
    )
    agent_communication_and_attitude_reason: str = Field(
        alias="agent_communication_&_attitude_reason"
    )
    agent_understanding_and_resolution: str = Field(
        alias="agent_understanding_&_resolution", description="0 to 5"
    )
    agent_understanding_and_resolution_reason: str = Field(
        alias="agent_understanding_&_resolution_reason"
    )
    agent_responsiveness: str = Field(description="0 to 5")
    agent_responsiveness_reason: str
    system_accessibility: bool
    system_accessibility_reason: str
    ivr_usability_and_design: bool = Field(alias="ivr_usability_&_design")
    ivr_usability_and_design_reason: str = Field(alias="ivr_usability_&_design_reason")
    ces: str = Field(description="1 to 5")
    self_service_readiness: Literal["High", "Medium", "Low"]
    cx_performance_insight: str


class Network(_SchemaModel):
    """Network fault reported on the call, if any, and where it occurred.

    Every field is required *and* nullable: the key must be present, but a call
    with no network complaint carries ``null``. Hence ``| None`` with no default
    — a default would drop the field from ``required``.
    """

    issue_type: (
        Literal[
            "Speed",
            "Outage",
            "Drop",
            "Coverage",
            "FUP",
            "Installation",
            "Support",
            "Voice Quality",
        ]
        | None
    )
    problem_statement: list[str]
    area_tag_province: str | None
    area_tag_district: str | None
    area_tag_sub_district: str | None
    area_tag_landmark: str | None


class ModelResponse(_SchemaModel):
    """Full analysis the model returns for a single customer-service call.

    The model never hears audio: its only evidence is a transcript of the whole
    call, already labelled ``Agent:`` / ``Customer:`` by an earlier call, and no
    acoustic measurement of any kind.

    This model carries **no** ``transcript`` field, unlike its google
    counterpart, and that absence is load-bearing rather than an oversight. On
    an OpenAI-compatible endpoint the schema reaches the model as a decoding
    grammar; ``Field(description=...)`` text is not reliably surfaced to it, so
    a transcript field specified only that way came back as a verbatim echo of
    the model's own input. Producing the transcript is now a separate,
    schema-free call -- see ``prompt/transcript_prompt.md`` -- and this model
    scores text it is *given*. Reintroducing the field here would restore the
    failure mode and pay ~2k completion tokens per file for it.
    """

    service_number: str
    call_type: list[Literal["Enquiry", "Service Request", "Complaint", "Sale", "Retention"]] = Field(
        description="1.Complaint, 2.Retention, 3.Service Request, 4.Enquiry, 5.Sale"
    )
    call_type_confident: str
    customer_insight: CustomerInsight
    service_quality: ServiceQuality
    sale_opportunity: SaleOpportunity
    customer_sentiment: CustomerSentiment
    customer_experience: CustomerExperience
    network: Network

    @field_validator("call_type", mode="before")
    @classmethod
    def _split_call_type(cls, value: Any) -> Any:
        """Accept the serialized comma string as input, not just the model's array.

        The model always returns an array, but ``_join_call_type`` dumps a string —
        so re-validating a saved ``model_dump()`` would otherwise fail on this model's
        own output. ``mode="before"`` runs ahead of the ``Literal`` check, so the tokens
        are still validated against the enum, and it leaves the JSON schema untouched.
        """
        if isinstance(value, str):
            return [token.strip() for token in value.split(",") if token.strip()]
        return value

    @field_serializer("call_type")
    def _join_call_type(self, value: list[str]) -> str:
        """Emit the call types as one comma-separated cell.

        The field stays a validated ``list[Literal[...]]`` in memory — which is what
        constrains generation and what ``response.parsed`` hands back — while every
        consumer downstream (the ``Series[str]`` output column, ``parse_call_types``)
        reads one string. A ``mode="after"`` validator that reassigned ``self.call_type``
        produces the same cell but warns ``PydanticSerializationUnexpectedValue`` on
        every dump, and breaks outright the moment ``validate_assignment`` is turned on.

        Serializers are invisible to ``model_json_schema()`` in its default validation
        mode, so both ``SchemaHelper`` renderings still describe an array.
        """
        return ",".join(value)


def _forbid_additional_properties(node: Any) -> Any:
    """Set ``additionalProperties: False`` on every object node, recursively.

    OpenAI's strict mode requires it on every object; the Gemini API rejects it, so it is
    injected on the OpenAI rendering only rather than via ``ConfigDict(extra="forbid")``.
    """
    if isinstance(node, dict):
        out = {k: _forbid_additional_properties(v) for k, v in node.items()}
        if out.get("type") == "object":
            out["additionalProperties"] = False
        return out
    if isinstance(node, list):
        return [_forbid_additional_properties(i) for i in node]
    return node


class SchemaHelper:
    """Renders :class:`ModelResponse` for each backend's schema dialect.

    Vertex and OpenAI want contradictory schema forms, so there is one renderer per target.
    For online google-genai calls, pass the class straight to ``GenerateContentConfig`` and
    read the typed result off ``response.parsed`` instead.
    """

    @staticmethod
    def vertex_schema() -> types.Schema:
        """Convert :class:`ModelResponse` to Vertex's OpenAPI-subset ``Schema``.

        ``from_json_schema`` inlines ``$defs`` (required: Vertex ingests batch JSONL via a
        BigQuery load, which rejects ``$``-prefixed keys), uppercases types, and folds
        ``anyOf: [X, null]`` into ``nullable: True`` -- hand-rolling that fold silently breaks
        nullable fields. ``raise_error_on_unsupported_field`` prevents silent field drops.
        """
        return types.Schema.from_json_schema(
            json_schema=types.JSONSchema.model_validate(ModelResponse.model_json_schema()),
            api_option="VERTEX_AI",
            raise_error_on_unsupported_field=True,
        )

    @staticmethod
    def vertex_generation_config() -> dict[str, Any]:
        """Return the ``generation_config`` block for a Vertex batch JSONL row.

        Batch rows are serialized ``GenerateContentRequest`` objects read straight off GCS
        (``inlined_requests`` is rejected on Vertex). ``by_alias=False`` emits snake_case, a
        convention choice -- ProtoJSON accepts both spellings.
        """
        return types.GenerationConfig(
            response_mime_type="application/json",
            response_schema=SchemaHelper.vertex_schema(),
        ).model_dump(exclude_none=True, mode="json", by_alias=False)

    @staticmethod
    def openai_response_format() -> dict[str, Any]:
        """Return the ``response_format`` payload for an OpenAI-compatible endpoint.

        ``union_format="primitive_type_array"`` gives OpenAI's documented nullable form;
        strict mode's every-property-required rule already holds (no field has a default),
        and ``$defs``/``$ref`` are left in place -- OpenAI supports them.
        """
        schema = ModelResponse.model_json_schema(union_format="primitive_type_array")
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "model_response",
                "strict": True,
                "schema": _forbid_additional_properties(schema),
            },
        }


__all__ = ["ModelResponse", "SchemaHelper"]
