"""Structured-output contract for the MNP cancellation-analysis prompt.

Converted item by item from ``prompt/system_prompt.txt``: that file is the
specification and this one is the decoding grammar, so the two must agree. Every
``Literal`` member below is copied verbatim from the prompt's quoted category
name, **including ``undefine``** (not ``undefined`` -- the retention sibling spells
it the other way, matching *its* prompt). The schema is what actually constrains
generation, so a tidied-up spelling here would silently override what the prompt
asks for. Fix those in both files at once, or in neither.

The model is deliberately **wide**: it carries the prompt's whole output, not just
the four columns the ``Voice_mnp - Groundtruth`` sheet scores. An earlier revision
trimmed it to the scored fields to save tokens; that made the benchmark cheap but
stopped it measuring the call production actually issues. The reasoning block, the
per-reason Thai keyword evidence, the event detection, the recommendation and the
network/location block are all back for that reason, and the extra fields are
deliberately *not* written to the result sheet -- they live in the prediction
shards, and the sheet keeps exactly the ground truth's columns so the join is
unchanged.

Two things are load-bearing, as in every sibling schema: **field declaration
order** (a model generates in ``properties`` insertion order, so ``transcript``
comes first, then the prompt's own numbering -- the reasoning is written down
before the reasons, and the reasons before the outcome conditioned on them) and
the absence of defaults (a default drops a field from ``required``, and OpenAI
strict mode demands every property be required). Never attach
``Field(description=...)`` to a model-typed field -- a bare ``$ref`` property is
replaced wholesale during schema conversion and the sibling description is
silently dropped.

``transcript`` is first for the same reason it is first in the QA sibling: the
model writes down what was said before it scores anything, so every field below is
conditioned on text it already committed to rather than on the audio alone. It is
uploaded beside the workbook as one ``.txt`` per call and scored by CER against the
human transcripts in ``gt_transcript_path``; it is not a sheet column.
``TRANSCRIPT_DESCRIPTION`` is a byte-for-byte copy of the QA schema's, and a test
asserts that -- the three pipelines must ask for the same transcript or their CER
numbers are not comparable.
"""

from __future__ import annotations

from typing import Any, Literal

from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

TRANSCRIPT_DESCRIPTION = """ถอดบทสนทนาทั้งสายจากไฟล์เสียงตามที่ได้ยินจริง โดยแยกผู้พูดออกเป็น 2 บทบาท คือ `Agent` (พนักงาน call center) และ `Customer` (ลูกค้า)

**1. Speaker identification (ต้องระบุจากน้ำเสียงและเนื้อหา ไม่ใช่จากลำดับการพูดหรือช่วงเงียบ):**
- ห้ามสมมติว่าผู้ที่พูดประโยคแรกคือ `Agent` เสมอไป ต้องตัดสินบทบาทของแต่ละฝ่ายจากเนื้อหา บทบาท และพฤติกรรมการพูดที่ปรากฏจริงในไฟล์เสียง แล้วจึงใช้บทบาทนั้นตลอดทั้งสาย
- ในสายมักมีช่วงเงียบ (dead air) เช่น พนักงานกำลังตรวจสอบข้อมูลในระบบ ลูกค้าหยุดคิด หรือสายเงียบชั่วคราว **ห้ามสมมติว่าเสียงที่พูดถัดจากช่วงเงียบเป็นของอีกฝ่ายเสมอไป** ผู้พูดคนเดิมอาจพูดต่อเองก็ได้ ให้ยึดจากลักษณะน้ำเสียงของผู้พูดและเนื้อหาที่พูดเป็นหลัก ไม่ใช่จากการมีช่วงเงียบคั่น
- หากผู้พูดคนเดิมพูดต่อหลังช่วงเงียบ ให้รวมไว้ในเทิร์นเดิม และห้ามใส่หมายเหตุช่วงเงียบ เช่น `[เงียบ]` หรือ `[dead air]` ลงในผลลัพธ์
- สัญญาณของ `Agent`: แจ้งชื่อบริษัท/ชื่อตนเองว่าเป็นพนักงาน, ขอยืนยันตัวตนลูกค้า (ชื่อ-นามสกุล / เลขบัตรประชาชน / เบอร์ที่ใช้บริการ), เสนอความช่วยเหลือ, ขอถือสายรอ (hold), อธิบายเงื่อนไข แพ็กเกจ ค่าบริการ หรือขั้นตอนดำเนินการ, เสนอขายหรือเสนอโปรโมชัน, ใช้ถ้อยคำสุภาพตามสคริปต์บริการ, เป็นฝ่ายควบคุมลำดับของบทสนทนาและกล่าวปิดการสนทนา
- สัญญาณของ `Customer`: แจ้งปัญหาหรือความต้องการ, ตอบคำถามยืนยันตัวตน, ให้ข้อมูลส่วนตัวของตนเอง, สอบถามสิทธิ์ ค่าบริการ หรือผลการดำเนินการ, แสดงอารมณ์ ความกังวล หรือความไม่พอใจ, ขอให้ดำเนินการแทน
- ใช้เกณฑ์เดียวกันทั้งสาย inbound (ลูกค้าโทรเข้า) และ outbound (พนักงานโทรออก) ผู้ที่เริ่มพูดก่อนเป็นฝ่ายใดก็ได้
- หากมีการโอนสายและมีพนักงานคนที่สองเข้ามาในสาย ให้ใช้ป้ายว่า `Agent` เหมือนกัน (ห้ามแยกเป็น Agent 1 / Agent 2)
- ข้ามเสียงที่ไม่ใช่บทสนทนาระหว่างสองฝ่าย เช่น เสียงระบบ IVR, เสียงประกาศอัตโนมัติ, เสียงเพลงรอสาย

**2. Fidelity (ห้ามเปลี่ยนแปลงถ้อยคำ):**
- ถอดตามที่ได้ยินจริงทุกคำ **ห้ามแปลภาษา ห้ามสรุป ห้ามย่อความ ห้ามเรียบเรียงใหม่ ห้ามแก้ไขไวยากรณ์หรือคำผิด**
- ใช้ภาษาเดียวกับที่พูดในไฟล์เสียงเสมอ พูดไทยให้ถอดเป็นไทย พูดอังกฤษให้ถอดเป็นอังกฤษ พูดปนกัน (code-switching) ให้คงไว้ตามที่พูด
- คงคำติดปาก คำซ้ำ คำลงท้าย และประโยคที่พูดไม่จบไว้ตามเดิม (เช่น "เอ่อ", "อ่า", "ค่ะ", "ครับ", "นะคะ")
- ห้ามเติมข้อความที่ไม่ได้พูด และห้ามเดา หากช่วงใดฟังไม่ชัดหรือไม่ได้ยิน ให้ใส่ `[ไม่ชัดเจน]` เฉพาะช่วงนั้น
- ถอดข้อมูลส่วนบุคคลที่พูดในสาย (ชื่อ เลขบัตรประชาชน เบอร์โทรศัพท์ ที่อยู่ เลขที่บัญชี) ตามที่ได้ยินจริง **ห้ามปิดบัง ห้ามแทนที่ด้วย placeholder และห้ามตัดออก** — กฎการใช้ placeholder ใช้กับ `summary_story` เท่านั้น ไม่ใช้กับฟิลด์นี้

**3. Output format:**
- หนึ่งเทิร์นต่อหนึ่งบรรทัด รูปแบบคือ `Agent: <ข้อความที่พูด>` หรือ `Customer: <ข้อความที่พูด>` ขึ้นบรรทัดใหม่ทุกครั้งที่เปลี่ยนผู้พูด **ห้ามเว้นบรรทัดว่างระหว่างเทิร์น**
- ใช้ป้ายชื่อเป็นภาษาอังกฤษว่า `Agent` และ `Customer` เท่านั้น แม้เนื้อหาที่พูดจะเป็นภาษาไทย
- รวมประโยคที่ผู้พูดคนเดียวกันพูดต่อเนื่องกันไว้ในเทิร์นเดียว และเรียงลำดับเทิร์นตามเวลาจริงในไฟล์เสียง
- ห้ามใส่ timestamp, เลขลำดับเทิร์น, หัวข้อ, markdown, code fence หรือคำอธิบายใด ๆ เพิ่มเติม
- หากไฟล์เสียงไม่มีบทสนทนา (สายเงียบ หรือสายหลุดก่อนเริ่มพูด) ให้ตอบเป็นข้อความว่าง
- ตัวอย่างรูปแบบผลลัพธ์ (ผู้เริ่มพูดเป็นฝ่ายใดก็ได้ ขึ้นกับไฟล์เสียงจริง):
Agent: <ข้อความที่พูดจริง>
Customer: <ข้อความที่พูดจริง>
Agent: <ข้อความที่พูดจริง>
"""

# The four call-result categories of the prompt's <result_definitions>, in prompt order.
# Exported for the same reason as ReasonCategory: the scorers read their label set off
# this, so the schema and the metrics cannot drift apart. "undefine" is the prompt's own
# spelling and is deliberately not corrected -- see the module docstring.
CallResultCategory = Literal["save", "churn", "unknown", "undefine"]

# The twelve cancellation-reason categories of the prompt's
# <reason_definitions_and_constraints>, in prompt order. Shared by main/secondary/third
# rather than written out three times, so the three ranks cannot drift apart.
#
# Exported: google_exact_match.py and google_confusion_matrix.py read their label sets
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

# The six categories of the prompt's <event_detection_definitions>, in prompt order and
# with the prompt's own bilingual labels. The retention sibling spells two of these
# differently (`Campaign-Drvien`, and no space before the bracket in `True-DTAC Merger(`)
# because *its* prompt does; neither spelling is corrected here.
EventCategory = Literal[
    "Market-Driven Events (เหตุการณ์ทางการตลาด)",
    "Crisis & Emergency Events (เหตุการณ์วิกฤตหรือภัยพิบัติ)",
    "Campaign-Driven Events (เหตุการณ์ด้านเคมเปญต่างๆของบริษัท)",
    "Technology & Service Events (เหตุการณ์ด้านเทคโนโลยี/บริการ)",
    "True-DTAC Merger (เหตุการณ์การรวมกิจการของ True และ Dtac)",
    "Emerging or Undefined Events (เหตุการณ์ที่ยังไม่สามารถจัดกลุ่มได้)",
]

# The eight network fault types of the prompt's <issue_type> block, in prompt order.
IssueTypeCategory = Literal[
    "Speed",
    "Outage",
    "Drop",
    "Coverage",
    "FUP",
    "Installation",
    "Support",
    "Voice Quality",
]

# The prompt's <product_type> block. Note this is the *network issue's* product, four
# members including `Prepaid` -- not the retention sibling's churning-product list, which
# has `unknown` instead. The two are different questions and must not be shared.
ProductTypeCategory = Literal["Postpaid", "Prepaid", "TVS", "TOL"]

AREA_TAG_DESCRIPTION = (
    "Only if the customer explicitly mentions this level; never inferred from another "
    "level. English transliteration of the Thai place name, or null."
)


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


class ReasonKeyword(_SchemaModel):
    """One ranked cancellation reason and the customer's own words behind it.

    The prompt's <output_requirements> item 3 asks for both together -- a category
    with no quoted evidence is what the validation checklist exists to catch -- so
    they are one object rather than two parallel fields.
    """

    reason: ReasonCategory = Field(
        description=(
            "The reason for customer cancellation, must be one of the cancellation "
            "categories defined in the prompt."
        )
    )
    keyword: str = Field(
        description=(
            "Specific Thai phrases extracted strictly from the customer's speech that "
            "support the reason for cancellation."
        )
    )


class Reasons(_SchemaModel):
    """The prompt's ranked cancellation reasons: Main > Secondary > Third.

    ``main`` is required -- a call the prompt asks to categorise always has a first
    reason. The second and third ranks the prompt marks Optional, so they are
    nullable; they carry no default, because a default would drop them from
    ``required`` and both API dialects want every property present, carrying an
    explicit ``null``.
    """

    main: ReasonKeyword
    secondary: ReasonKeyword | None
    third: ReasonKeyword | None

    @model_validator(mode="after")
    def remove_duplicate_reasons(self) -> Reasons:
        """Collapse a lower rank that merely repeats a higher one, keeping its evidence.

        The ground-truth sheet never lists the same category twice across the three
        ranks, so a repeat is always wrong. The duplicate's keyword is folded into the
        surviving rank rather than discarded -- the phrase is real evidence for a
        category the model did choose -- and the rank itself is nulled, which scores as
        "no further reason" instead of costing a second column as well.

        Checked against ``secondary`` last and only while it survives, so a response
        whose three ranks are all the same category collapses to ``main`` alone rather
        than leaving ``third`` behind a nulled ``secondary``.
        """
        if self.secondary is not None and self.secondary.reason == self.main.reason:
            self.main.keyword = f"{self.main.keyword}, {self.secondary.keyword}"
            self.secondary = None
        if self.third is not None and self.third.reason == self.main.reason:
            self.main.keyword = f"{self.main.keyword}, {self.third.keyword}"
            self.third = None
        if (
            self.secondary is not None
            and self.third is not None
            and self.third.reason == self.secondary.reason
        ):
            self.secondary.keyword = f"{self.secondary.keyword}, {self.third.keyword}"
            self.third = None
        return self


class AdditionalFields(_SchemaModel):
    """The prompt's <additional_context>: the network fault behind the call, if any.

    ``issue_type`` is the trigger for the whole block, exactly as the prompt's
    <activation_rule> states it: when it holds a value every other field here holds
    one too, and the block goes all-``null`` only when it is ``null``. A neutral or
    positive call is explicitly *not* a reason to null it out -- the prompt calls
    that mistake out by name, and :meth:`_null_dependents_without_an_issue` enforces
    only the one direction the rule actually specifies.

    Every field is required *and* nullable: the key must always be present, hence
    ``| None`` with no default.
    """

    issue_type: IssueTypeCategory | None
    sub_reason: str | None = Field(
        description=(
            "English explanation of the specific network issue -- the symptom, how often "
            "it occurs, and what it stops the customer doing. Maximum 800 characters."
        )
    )
    problem_statement_list: list[str] | None = Field(
        description=(
            "The customer's own Thai sentences describing the network problem or its "
            "impact, quoted exactly from their speech and not the agent's. At least one "
            "when issue_type is set."
        )
    )
    churn_probability: int | None = Field(
        description=(
            "0 to 100: how likely this customer is to churn, judged from the severity of "
            "the network issue and their own statements on this call alone."
        )
    )
    area_tag_province: str | None = Field(description=AREA_TAG_DESCRIPTION)
    area_tag_district: str | None = Field(description=AREA_TAG_DESCRIPTION)
    area_tag_sub_district: str | None = Field(description=AREA_TAG_DESCRIPTION)
    area_tag_landmark: str | None = Field(description=AREA_TAG_DESCRIPTION)
    product_type: ProductTypeCategory | None = Field(
        description=(
            "The product the network issue affects: `Postpaid` monthly mobile, `Prepaid` "
            "pay-as-you-go, `TVS` True Vision, `TOL` True Online home internet. null only "
            "when it genuinely cannot be determined."
        )
    )

    @model_validator(mode="after")
    def _null_dependents_without_an_issue(self) -> AdditionalFields:
        """Enforce the prompt's activation rule: no ``issue_type`` means no block.

        Only this direction is enforced. The reverse -- filling the dependents in when
        ``issue_type`` *is* set -- cannot be done here without inventing content, so a
        half-filled block stays as the model returned it and is visible in the shard.
        """
        if self.issue_type is None:
            self.sub_reason = None
            self.problem_statement_list = None
            self.churn_probability = None
            self.area_tag_province = None
            self.area_tag_district = None
            self.area_tag_sub_district = None
            self.area_tag_landmark = None
            self.product_type = None
        return self

    @computed_field
    @property
    def problem_statement(self) -> str | None:
        """The statement list joined into one cell, for consumers that want a string.

        A computed field, not a generated one: the old schema asked the model for this
        and then overwrote it in a validator, so every call paid for tokens that were
        thrown away. Computed fields are absent from ``model_json_schema()`` in its
        default validation mode, which is what both ``SchemaHelper`` renderings use, so
        the model is never asked for it -- but it is present in every ``model_dump()``.
        """
        return ", ".join(self.problem_statement_list) if self.problem_statement_list else None


class ModelResponse(_SchemaModel):
    """The MNP cancellation analysis the model returns for one call.

    Declaration order is generation order and follows the prompt: the transcript, then
    the step-by-step reasoning, then the ranked reasons, then the outcome conditioned on
    them, and last the advisory fields that depend on everything above.
    """

    transcript: str = Field(description=TRANSCRIPT_DESCRIPTION)
    reasoning: str = Field(
        description=(
            "Analyze the conversation step-by-step in a logical manner. Consider the "
            "customer's tone, the call center agent's responses, and the flow of the "
            "conversation. Explain your thought process before concluding the final "
            "result."
        )
    )
    reasons: Reasons
    call_result: CallResultCategory = Field(
        description=(
            "The customer's final decision after the agent's retention effort, judged "
            "from the whole call: `save` to stay, `churn` to cancel, `unknown` when the "
            "call ends undecided or is cut off, `undefine` when the call was never about "
            "porting out at all."
        )
    )
    call_event_detection: EventCategory | None = Field(
        description=(
            "The event that may have influenced the customer's decision to cancel. null "
            "when the call points to none of the categories."
        )
    )
    ai_recommendation: str | None = Field(
        description=(
            "Short recommendations in Thai for retaining customers, improving service "
            "quality or addressing the issues raised on this call. null when the call "
            "gives nothing to recommend on."
        )
    )
    additional_fields: AdditionalFields


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
    "AdditionalFields",
    "CallResultCategory",
    "EventCategory",
    "IssueTypeCategory",
    "ModelResponse",
    "ProductTypeCategory",
    "ReasonCategory",
    "ReasonKeyword",
    "Reasons",
    "SchemaHelper",
    "TRANSCRIPT_DESCRIPTION",
]
