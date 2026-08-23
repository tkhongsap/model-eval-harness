"""Structured-output contract for the MNP cancellation-analysis prompt (local model).

Field-for-field identical to
:mod:`src.google_model.sentiment_mnp.schema.model_response`, and deliberately so:
the two pipelines are scored against the same ``Voice_mnp - Groundtruth`` sheet, so
a field that existed on one side and not the other would make the comparison
meaningless. Only the *prompts* diverge -- see ``prompt/system_prompt.txt`` here
versus google's -- because this model never hears audio; its only evidence is an
ASR transcript that a separate labelling call has split into ``Agent:`` /
``Customer:`` turns. Change a ``Literal`` here and you must change it in the google
copy too, or the two runs stop being comparable.

Converted item by item from ``prompt/system_prompt.txt``: that file is the
specification and this one is the decoding grammar, so the two must agree. Every
``Literal`` member below is copied verbatim from the prompt's quoted category
name, **including ``undefine``** (not ``undefined`` -- the retention sibling spells
it the other way, matching *its* prompt). The schema is what actually constrains
generation, so a tidied-up spelling here would silently override what the prompt
asks for. Fix those in both files at once, or in neither.

The model is deliberately **narrow**: it carries only the fields the
``Voice_mnp - Groundtruth`` sheet scores -- the three ranked cancellation reasons
and the call result. The prompt's event detection, recommendation, chain-of-thought
and network-issue blocks were all removed from prompt and schema together, because
nothing downstream reads them and every generated token is paid for.

Two things are load-bearing, as in every sibling schema: **field declaration
order** (a model generates in ``properties`` insertion order, so ``reasons`` comes
before ``call_result`` -- the prompt ranks the reasons first and conditions the
outcome on them) and the absence of defaults (a default drops a field from
``required``, and OpenAI strict mode demands every property be required). Never
attach ``Field(description=...)`` to a model-typed field -- a bare ``$ref``
property is replaced wholesale during schema conversion and the sibling
description is silently dropped.

This model carries **no** ``transcript`` field, for two reasons that happen to
agree. The MNP ground-truth sheet has no transcript column to score against; and
in this pipeline the transcript is *input*, produced by the ASR and labelling legs
before this grammar is ever applied. It is uploaded beside the workbook as a
``.txt`` per call, not carried in the decoded response.
"""

from __future__ import annotations

from typing import Any, Literal

from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, model_validator

# The four call-result categories of the prompt's <result_definitions>, in prompt order.
# Exported for the same reason as ReasonCategory: the scorers read their label set off
# this, so the schema and the metrics cannot drift apart. "undefine" is the prompt's own
# spelling and is deliberately not corrected -- see the module docstring.
CallResultCategory = Literal["save", "churn", "unknown", "undefine"]

# The twelve cancellation-reason categories of the prompt's
# <reason_definitions_and_constraints>, in prompt order. Shared by main/secondary/third
# rather than written out three times, so the three ranks cannot drift apart.
#
# Exported: internal_exact_match.py and internal_confusion_matrix.py read their label sets
# off this via typing.get_args, so the scored vocabulary has exactly one definition.
# Note "true point, dtac reward" is an MNP-only category -- the retention prompt folds
# that case into `other`, so its ReasonCategory has eleven members, not twelve.
ReasonCategory = Literal[
    "network",
    "promotion related",
    "device promotion related",
    "save cost",
    "contract end",
    "sale upsell problem",
    "dissatisfied service",
    "post to pre",
    "customer reason",
    "true point, dtac reward",
    "down sell not success",
    "other",
]


def _drop_docstring_description(schema: dict[str, Any]) -> None:
    """Keep class docstrings out of the schema handed to the model.

    Pydantic promotes a model's docstring to the object's ``description``; only
    ``Field(description=...)`` is prompt text.
    """
    schema.pop("description", None)


class _SchemaModel(BaseModel):
    """Base for every response model: alias-transparent in and out.

    ``serialize_by_alias`` means a plain ``model_dump()`` already emits the
    property names the prompt uses, so no caller can forget ``by_alias=True``.

    ``extra="forbid"`` is deliberately *not* set -- it would emit
    ``additionalProperties``, which the Gemini Developer API rejects.
    """

    model_config = ConfigDict(
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
        json_schema_extra=_drop_docstring_description,
    )


class Reasons(_SchemaModel):
    """The prompt's ranked cancellation reasons: Main > Secondary > Third.

    ``main`` is required -- a call the prompt asks to categorise always has a first
    reason. The second and third ranks the prompt marks Optional, so they are
    nullable; they carry no default, because a default would drop them from
    ``required`` and both API dialects want every property present, carrying an
    explicit ``null``.
    """

    main: ReasonCategory = Field(
        description=(
            "The single most critical factor driving the customer's decision (the root "
            "cause). Must be justified by the customer's own speech, not the agent's."
        )
    )
    secondary: ReasonCategory | None = Field(
        description=(
            "The second reason -- the context, method or trigger behind the main one. "
            "null when the call gives only one reason."
        )
    )
    third: ReasonCategory | None = Field(
        description=(
            "The third reason -- a minor complaint mentioned in passing. null when the "
            "call gives fewer than three reasons."
        )
    )

    @model_validator(mode="after")
    def remove_duplicate_reasons(self) -> Reasons:
        """Collapse a lower rank that merely repeats a higher one.

        The ground-truth sheet never lists the same category twice across the three
        ranks, so a repeat is always wrong. Nulling it scores as "no further reason",
        which is the closer answer, rather than costing a second column as well.

        Checked against ``secondary`` last and only while it survives, so a response
        whose three ranks are all the same category collapses to ``main`` alone rather
        than leaving ``third`` behind a nulled ``secondary``.
        """
        if self.secondary is not None and self.secondary == self.main:
            self.secondary = None
        if self.third is not None and self.third == self.main:
            self.third = None
        if self.secondary is not None and self.third is not None and self.third == self.secondary:
            self.third = None
        return self


class ModelResponse(_SchemaModel):
    """The MNP cancellation analysis the model returns for one call.

    ``reasons`` is declared first so generation follows the prompt's own order: the
    ranked reasons are written down before the outcome is conditioned on them.
    """

    reasons: Reasons
    call_result: CallResultCategory = Field(
        description=(
            "The customer's final decision after the agent's retention effort, judged "
            "from the whole call: `save` to stay, `churn` to cancel, `unknown` when the "
            "call ends undecided or is cut off, `undefine` when the call was never about "
            "porting out at all."
        )
    )


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


__all__ = ["CallResultCategory", "ModelResponse", "ReasonCategory", "Reasons", "SchemaHelper"]
