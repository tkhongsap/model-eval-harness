"""Structured-output contract for the retention cancellation-analysis prompt (local model).

Shares its **shape** with :mod:`src.google_model.sentiment_retention.schema.model_response` and
its vocabulary exactly -- the two pipelines are scored against the same
``Voice_retention - Groundtruth`` sheet by the same scorer, so a divergence in either would make
the comparison meaningless. Change a ``Literal`` here and you must change it in the google copy
too. Only the *prompts* diverge, because this model never hears audio: its only evidence is an
ASR transcript that a separate labelling call has split into ``Agent:`` / ``Customer:`` turns.

The model is deliberately **narrow**: it carries only what the ground-truth sheet scores -- the
churning products, their ranked cancellation reasons and their retention outcomes. The google
copy additionally carries ``transcript``, ``call_event_detection``, ``recommendation`` and the
per-product ``network_issue`` block, and its ranks pair each reason with the client's own
keywords. None of that is scored, and every generated token is paid for on an endpoint whose
cost is dominated by transcription. That narrowing is a decision, pinned by a test, and it is
about fields this schema never carried -- not about falling behind a change made to the google
copy. The per-product grain **is** such a change, and is mirrored here.

The shape is **per product**, matching production: a client can churn from Postpaid while being
saved on TOL, with different reasons on each. An earlier revision flattened this to one answer
per call, which is why the ground-truth tab's calls graded on two rows could not be scored.

Two things are load-bearing, as in every sibling schema: **field declaration order** (a model
generates in ``properties`` insertion order, so the ranked reasons come before the outcome that
is conditioned on them) and the absence of defaults (a default drops a field from ``required``,
and OpenAI strict mode demands every property be required). Never attach
``Field(description=...)`` to a model-typed field -- a bare ``$ref`` property is replaced
wholesale during schema conversion and the sibling description is silently dropped.

This model carries **no** ``transcript`` field, for two reasons that happen to agree. The
retention ground-truth sheet has no transcript column to score against; and in this pipeline the
transcript is *input*, produced by the ASR and labelling legs before this grammar is ever
applied. It is uploaded beside the workbook as a ``.txt`` per call, not carried in the decoded
response.
"""

from __future__ import annotations

from typing import Any, Literal

from google.genai import types
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

# The eleven cancellation-reason categories of prompt item 2, in prompt order.
# Shared by main/secondary/third rather than written out three times, so the three
# ranks cannot drift apart.
#
# Exported: internal_exact_match.py and internal_confusion_matrix.py read their label sets
# off this via typing.get_args, so the scored vocabulary has exactly one definition.
# There is deliberately no "true point, dtac reward" member -- the MNP sibling has one,
# while this prompt folds that case into `other` (see item 2's `other` definition).
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
    "down sell not success",
    "other",
]


# Prompt item 3. Exported for the same reason as ReasonCategory: the scorers read their
# label set off this, so the schema and the metrics cannot drift apart.
RetentionOutcomeCategory = Literal["churn", "save", "unknown", "undefined"]

# Prompt item 1. `unknown` is a real graded value, not a gap: the ground-truth sheet
# uses it once in `call_sumary_product`, so the prompt tells the model to answer with
# it rather than leaving the field empty.
ProductCategory = Literal["Postpaid", "TOL", "TVS", "unknown"]


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

    ``extra="forbid"`` is deliberately *not* set — it would emit
    ``additionalProperties``, which the Gemini Developer API rejects.
    """

    model_config = ConfigDict(
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
        json_schema_extra=_drop_docstring_description,
    )


PRODUCT_DESCRIPTION = (
    "การวิเคราะห์ของบริการนี้ ใส่ค่าเฉพาะบริการที่ถูกพูดถึงในสายเท่านั้น "
    "บริการที่ไม่ได้ถูกพูดถึงให้เป็น null ทั้งก้อน"
)


class ProductAnalysis(_SchemaModel):
    """Everything the model concludes about **one** churning product.

    The narrow twin of the google package's class of the same name: the same three ranks and the
    same outcome, without the ``keyword`` evidence field on each rank or the ``network_issue``
    block. Declaration order is generation order -- the reasons are written down before the
    outcome that is conditioned on them.

    Every rank is nullable and none carries a default: the key is always present and an absent
    rank carries an explicit ``null``, which is what both API dialects want.
    """

    main: ReasonCategory | None = Field(description="เหตุผลหลักที่ลูกค้าต้องการยกเลิกบริการนี้")
    secondary: ReasonCategory | None = Field(
        description="เหตุผลที่สองของบริการนี้ หากไม่มีให้เป็น null"
    )
    third: ReasonCategory | None = Field(
        description="เหตุผลที่สามของบริการนี้ หากไม่มีให้เป็น null"
    )
    retention_outcome: RetentionOutcomeCategory = Field(
        description=(
            "ผลลัพธ์การรักษาลูกค้าสำหรับบริการนี้โดยเฉพาะ ตัดสินจากช่วงท้ายของบทสนทนา "
            "ลูกค้าอาจตัดสินใจต่างกันในแต่ละบริการ"
        )
    )

    @model_validator(mode="after")
    def remove_duplicate_reasons(self) -> ProductAnalysis:
        """Collapse a lower rank that merely repeats a higher one.

        The ground-truth sheet never lists the same category twice within a product, so a repeat
        is always wrong. Nulling it scores as "no further reason", which is the closer answer.

        Checked against ``secondary`` last and only while it survives, so a product whose three
        ranks are all the same category collapses to ``main`` alone rather than leaving ``third``
        behind a nulled ``secondary``.
        """
        if self.secondary is not None and self.secondary == self.main:
            self.secondary = None
        if self.third is not None and self.third == self.main:
            self.third = None
        if self.secondary is not None and self.third is not None and self.third == self.secondary:
            self.third = None
        return self


class ProductMap(_SchemaModel):
    """The four products, each either analysed or explicitly absent.

    Production declares this object with no ``required`` list, so the model returns only the
    products the call mentioned. Strict mode forbids an optional property, so all four keys are
    present and an unmentioned product carries an explicit ``null``.
    """

    Postpaid: ProductAnalysis | None = Field(description=PRODUCT_DESCRIPTION)
    TOL: ProductAnalysis | None = Field(description=PRODUCT_DESCRIPTION)
    TVS: ProductAnalysis | None = Field(description=PRODUCT_DESCRIPTION)
    unknown: ProductAnalysis | None = Field(description=PRODUCT_DESCRIPTION)

    def analysed(self) -> dict[str, ProductAnalysis]:
        """The products this call actually named, keyed by product name, in declaration order.

        The one place the map is turned back into a sequence, so the output pipeline and the
        tests cannot disagree about which products a response covers or what order they land in.
        """
        return {
            name: value
            for name, value in (
                ("Postpaid", self.Postpaid),
                ("TOL", self.TOL),
                ("TVS", self.TVS),
                ("unknown", self.unknown),
            )
            if value is not None
        }


class ModelResponse(_SchemaModel):
    """The retention analysis the model returns for one call."""

    product: ProductMap


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


__all__ = [
    "ModelResponse",
    "ProductAnalysis",
    "ProductCategory",
    "ProductMap",
    "ReasonCategory",
    "RetentionOutcomeCategory",
    "SchemaHelper",
]
