"""Excel-only projection of :class:`ModelResponse` for the trim analysis pipeline.

``internal_asr_llm_trim_output.py`` decodes into this narrowed model instead of the
full :class:`~src.local_model.sentiment.schema.model_response.ModelResponse`, and
sends the matching *trimmed* prompt files (built by ``scripts/build_trim_prompts.py``)
so neither the rubric nor the grammar mentions any field the output sheet discards:
every per-criterion ``reason``, ``omotenashi`` (the one criterion with no sheet
column), ``service_number``, ``call_type_confident``, all of ``SaleOpportunity`` /
``CustomerExperience`` / ``Network``, and the insight/CSAT extras of
``CustomerInsight`` and ``CustomerSentiment``. What remains maps 1:1 onto
``OutputSchema``'s data columns.

The same two rules as the full schema are load-bearing here: **field declaration
order** is generation order (do not alphabetize or regroup — it mirrors
``ModelResponse``'s), and never attach ``Field(description=...)`` to a model-typed
field, whose sibling description would be silently dropped during schema conversion.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_serializer, field_validator

from src.local_model.sentiment.schema.model_response import _SchemaModel


class TrimCriterionEvaluation(_SchemaModel):
    """Verdict for a service-quality criterion that may be inapplicable — no reason."""

    evaluation: Literal["Meet", "Below", "N/A"]


class TrimBinaryCriterionEvaluation(_SchemaModel):
    """Verdict for a criterion that always applies, so there is no ``N/A`` — no reason."""

    evaluation: Literal["Meet", "Below"]


class TrimCustomerInsight(_SchemaModel):
    """Only the one ``CustomerInsight`` field the sheet keeps."""

    summary_story: str = Field(description="บทสรุปการสนทนา สรุปเป็นภาษาไทย")


class TrimServiceQuality(_SchemaModel):
    """The 22 criteria with sheet columns, in ``ServiceQuality``'s declaration order.

    Dropped relative to the full model: ``omotenashi`` (no sheet column) and
    ``service_quality_performance_insight``. Each criterion keeps its original
    binary-vs-``N/A``-capable evaluation domain, minus the ``reason`` text.
    """

    greeting_standard: TrimCriterionEvaluation
    manners: TrimBinaryCriterionEvaluation
    enthusiasm: TrimBinaryCriterionEvaluation
    communication_skill: TrimBinaryCriterionEvaluation
    ending_standard: TrimCriterionEvaluation
    data_privacy: TrimCriterionEvaluation
    legal_verification: TrimCriterionEvaluation
    customer_verification: TrimCriterionEvaluation
    sla_notification: TrimCriterionEvaluation
    transfer_standard: TrimCriterionEvaluation
    problem_understanding: TrimBinaryCriterionEvaluation
    compensation: TrimCriterionEvaluation
    hold_standard: TrimCriterionEvaluation
    wrap_up: TrimCriterionEvaluation
    beyond_scope_support: TrimCriterionEvaluation
    true_application: TrimCriterionEvaluation
    case_ownership: TrimCriterionEvaluation
    contact_confirm: TrimCriterionEvaluation
    retention: TrimCriterionEvaluation
    downsell: TrimCriterionEvaluation
    mnp: TrimCriterionEvaluation
    upselling: TrimCriterionEvaluation


class TrimCustomerSentiment(_SchemaModel):
    """The three sentiment verdicts the sheet keeps — no driver, CSAT, or insight."""

    overall_sentiment: Literal["Positive", "Neutral", "Negative"]
    initial_sentiment: Literal["Positive", "Neutral", "Negative"]
    final_sentiment: Literal["Positive", "Neutral", "Negative"]


class TrimModelResponse(_SchemaModel):
    """Sheet-bound analysis of one call: the Excel projection of ``ModelResponse``.

    Field order preserves the full model's relative order. The ``call_type``
    validator/serializer pair is copied verbatim from ``ModelResponse``: the
    ``mode="before"`` validator lets the resume path re-validate a stored dump
    (whose ``call_type`` is the joined comma string), and the serializer is what
    ``build_output_df`` relies on for the single ``call_type`` cell.
    """

    call_type: list[Literal["Enquiry", "Service Request", "Complaint", "Sale", "Retention"]] = Field(
        description="1.Complaint, 2.Retention, 3.Service Request, 4.Enquiry, 5.Sale"
    )
    customer_insight: TrimCustomerInsight
    service_quality: TrimServiceQuality
    customer_sentiment: TrimCustomerSentiment

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
        mode, so a schema rendering still describes an array.
        """
        return ",".join(value)


__all__ = [
    "TrimBinaryCriterionEvaluation",
    "TrimCriterionEvaluation",
    "TrimCustomerInsight",
    "TrimCustomerSentiment",
    "TrimModelResponse",
    "TrimServiceQuality",
]
