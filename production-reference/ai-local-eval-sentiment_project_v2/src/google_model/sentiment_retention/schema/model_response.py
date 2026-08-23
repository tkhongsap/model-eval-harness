"""Structured-output contract for the retention cancellation-analysis prompt.

Converted item by item from ``prompt/system_prompt.md``: that file is the
specification and this one is the decoding grammar, so the two must agree. Every
``Literal`` member below is copied verbatim from the prompt's backticked label,
**including its typos** (``Campaign-Drvien``, and the missing space in
``True-DTAC Merger(``). The schema is what actually constrains generation, so a
tidied-up spelling here would silently override what the prompt asks for and stop
matching a ground-truth sheet keyed on the prompt's wording. Fix those in both
files at once, or in neither.

The model is deliberately **wide**: it carries the prompt's whole output (items
1-13), not just the five columns the ``Voice_retention - Groundtruth`` sheet
scores. An earlier revision trimmed it to the scored fields to save tokens; that
made the benchmark cheap but stopped it measuring the call production actually
issues. The customer's own quoted phrases, the event detection, the recommendation
and the network/location block are all back for that reason, and the extra fields
are deliberately *not* written to the result sheet -- they live in the prediction
shards, and the sheet keeps exactly the ground truth's columns so the join is
unchanged.

The shape here is **flat**, one answer per call. An earlier draft of the prompt
showed a per-product nesting with an outcome per product; the ground-truth sheet is
one row per call with one outcome, so the prompt's example was rewritten to match
this model rather than the other way round.

Two things are load-bearing, as in every sibling schema: **field declaration
order** (a model generates in ``properties`` insertion order, so ``transcript``
comes first and the rest follow the prompt's 1-13 numbering; they must not be
alphabetized or regrouped) and the absence of defaults (a default drops a field
from ``required``, and OpenAI strict mode demands every property be required).
Never attach ``Field(description=...)`` to a model-typed field -- a bare ``$ref``
property is replaced wholesale during schema conversion and the sibling
description is silently dropped.

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
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

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

# The eleven cancellation-reason categories of prompt item 2, in prompt order.
# Shared by main/secondary/third rather than written out three times, so the three
# ranks cannot drift apart.
#
# Exported: google_exact_match.py and google_confusion_matrix.py read their label sets
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
# it rather than leaving the field empty. Note this is the *churning* product and has no
# `Prepaid` member, unlike the MNP sibling's network-issue `ProductTypeCategory`.
ProductCategory = Literal["Postpaid", "TOL", "TVS", "unknown"]

# The six categories of prompt item 4, with the prompt's own labels and its own two
# typos. The MNP sibling spells the same events correctly because *its* prompt does;
# neither is corrected here.
EventCategory = Literal[
    "Market-Driven Events (เหตุการณ์ทางการตลาด)",
    "Crisis & Emergency Events (เหตุการณ์วิกฤตหรือภัยพิบัติ)",
    "Campaign-Drvien Events (เหตุการณ์ด้านเคมเปญต่างๆของบริษัท)",
    "Technology & Service Events (เหตุการณ์ด้านเทคโนโลยี/บริการ)",
    "True-DTAC Merger(การรวมกิจการของ True และ ดีแทค)",
    "Emerging or Undefined Events (เหตุผลที่ยังไม่สามารถจัดกลุ่มได้)",
]

# The eight network fault types of prompt item 6, in prompt order.
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

# Production's own wording, from the `keyword` property of PRODUCT_REASON_SCHEMA. Note this
# asks for comma-separated keywords, not one quoted sentence: production's *prompt example* says
# "Phrase" and its *schema* says "keyword", and function calling binds to the schema, so `keyword`
# is what production actually receives and writes to its `keyword(main)` columns.
KEYWORD_DESCRIPTION = (
    "List keywords or short phrases directly from the audio that explicitly indicate or support "
    "the reason, quoting the client and never the agent. Use comma separation. null if the rank "
    "itself is null."
)

AREA_TAG_DESCRIPTION = (
    "Only if the client explicitly mentions this level; never inferred from another "
    "level. English transliteration of the Thai place name, or null."
)

REASON_DESCRIPTION = "เหตุผลที่ลูกค้าต้องการยกเลิกบริการนี้ หากไม่มีเหตุผลในลำดับนี้ให้เป็น null"

MAIN_REASON_DESCRIPTION = "เหตุผลหลักที่ลูกค้าต้องการยกเลิกบริการนี้"

SECONDARY_REASON_DESCRIPTION = "เหตุผลที่สองของบริการนี้ หากไม่มีให้เป็น null"

THIRD_REASON_DESCRIPTION = "เหตุผลที่สามของบริการนี้ หากไม่มีให้เป็น null"

OUTCOME_DESCRIPTION = (
    "ผลลัพธ์การรักษาลูกค้าสำหรับบริการนี้โดยเฉพาะ ตัดสินจากช่วงท้ายของบทสนทนา "
    "ลูกค้าอาจตัดสินใจต่างกันในแต่ละบริการ"
)

PRODUCT_DESCRIPTION = (
    "การวิเคราะห์ของบริการนี้ ใส่ค่าเฉพาะบริการที่ถูกพูดถึงในสายเท่านั้น "
    "บริการที่ไม่ได้ถูกพูดถึงให้เป็น null ทั้งก้อน"
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

    ``extra="forbid"`` is deliberately *not* set — it would emit
    ``additionalProperties``, which the Gemini Developer API rejects.
    """

    model_config = ConfigDict(
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
        json_schema_extra=_drop_docstring_description,
    )


class ReasonSlot(_SchemaModel):
    """One ranked cancellation reason and the client's own words for it.

    Production's ``PRODUCT_REASON_SCHEMA``: a ``{reason, keyword}`` pair, repeated at each of the
    three ranks inside every product. The pairing is the point -- the category and the evidence
    for it are generated together, rather than three categories followed by three quotations that
    the model has to re-associate.

    Both fields are nullable and neither carries a default. Production marks ``secondary`` and
    ``third`` optional at the object level; strict mode forbids an optional property, so the key
    is always present and carries an explicit ``null`` instead. Same meaning, legal schema.
    """

    reason: ReasonCategory | None = Field(description=REASON_DESCRIPTION)
    keyword: str | None = Field(description=KEYWORD_DESCRIPTION)


class Area(_SchemaModel):
    """Where the network fault is, as far as the client actually said.

    Production nests these four under an ``area`` object rather than hoisting them beside
    ``churn_probability``; an earlier conversion here flattened them, which changed the shape of
    every prediction shard against production's.

    Every level is independent and never inferred from another -- a named district does not imply
    its province unless the client said so.
    """

    area_tag_province: str | None = Field(description=AREA_TAG_DESCRIPTION)
    area_tag_district: str | None = Field(description=AREA_TAG_DESCRIPTION)
    area_tag_sub_district: str | None = Field(description=AREA_TAG_DESCRIPTION)
    area_tag_landmark: str | None = Field(description=AREA_TAG_DESCRIPTION)


class NetworkIssue(_SchemaModel):
    """Prompt items 6-13: the network fault behind the call, if there is one.

    ``issue_type`` is the trigger for the whole block: when it holds a value every
    other field here holds one too, and the block goes all-``null`` only when it is
    ``null``. A neutral or positive call is explicitly *not* a reason to null it
    out -- the prompt calls that mistake out by name, and
    :meth:`_null_dependents_without_an_issue` enforces only the one direction the
    rule actually specifies.

    Every field is required *and* nullable: the key must always be present, hence
    ``| None`` with no default.
    """

    issue_type: IssueTypeCategory | None
    sub_reason: str | None = Field(
        description="English explanation of the specific issue, maximum 800 characters."
    )
    problem_statement_list: list[str] | None = Field(
        description=(
            "The client's own Thai sentences describing the problem or its impact, "
            "quoted exactly. At least one when issue_type is set."
        )
    )
    churn_probability: int | None = Field(
        description="0 to 100: how likely this client is to churn, judged from this call alone."
    )
    area: Area

    @model_validator(mode="after")
    def _null_dependents_without_an_issue(self) -> NetworkIssue:
        """Enforce the prompt's activation rule: no ``issue_type`` means no block.

        Only this direction is enforced. The reverse -- filling the dependents in when
        ``issue_type`` *is* set -- cannot be done here without inventing content, so a
        half-filled block stays as the model returned it and is visible in the shard.
        """
        if self.issue_type is None:
            self.sub_reason = None
            self.problem_statement_list = None
            self.churn_probability = None
            self.area = Area(
                area_tag_province=None,
                area_tag_district=None,
                area_tag_sub_district=None,
                area_tag_landmark=None,
            )
        return self


class ProductAnalysis(_SchemaModel):
    """Everything the model concludes about **one** churning product.

    Production's ``PRODUCT_ANALYSIS_SCHEMA``. The nesting is the point and the reason this class
    exists: a client can churn from Postpaid while being saved on TOL, with different reasons on
    each, and a single flat answer per call cannot express that. An earlier revision here did
    flatten it, which is why the ground-truth tab's three two-row calls could not be scored.

    Declaration order is generation order: the ranked reasons are written down before the outcome,
    so the verdict is conditioned on the evidence rather than the other way round.
    """

    main: ReasonSlot = Field(description=MAIN_REASON_DESCRIPTION)
    secondary: ReasonSlot = Field(description=SECONDARY_REASON_DESCRIPTION)
    third: ReasonSlot = Field(description=THIRD_REASON_DESCRIPTION)
    retention_outcome: RetentionOutcomeCategory = Field(description=OUTCOME_DESCRIPTION)
    network_issue: NetworkIssue

    @model_validator(mode="after")
    def remove_duplicate_reasons(self) -> ProductAnalysis:
        """Collapse a lower rank that merely repeats a higher one, keeping its evidence.

        The ground-truth sheet never lists the same category twice within a product, so a repeat
        is always wrong. The duplicate's keywords are folded into the surviving rank rather than
        discarded -- they are real evidence for a category the model did choose -- and the rank
        and its keywords are nulled together, which scores as "no further reason" instead of
        costing a second column as well.

        Checked against ``secondary`` last and only while it survives, so a product whose three
        ranks are all the same category collapses to ``main`` alone rather than leaving ``third``
        behind a nulled ``secondary``.

        There is no production counterpart on the retention side -- production tolerates repeats
        because it set-unions before scoring. It is kept because the *shard* is read by people,
        and a response naming `network` three times is noise in it either way.
        """
        if self.secondary.reason is not None and self.secondary.reason == self.main.reason:
            self.main.keyword = _merge_keywords(self.main.keyword, self.secondary.keyword)
            self.secondary = ReasonSlot(reason=None, keyword=None)
        if self.third.reason is not None and self.third.reason == self.main.reason:
            self.main.keyword = _merge_keywords(self.main.keyword, self.third.keyword)
            self.third = ReasonSlot(reason=None, keyword=None)
        if (
            self.secondary.reason is not None
            and self.third.reason is not None
            and self.third.reason == self.secondary.reason
        ):
            self.secondary.keyword = _merge_keywords(
                self.secondary.keyword, self.third.keyword
            )
            self.third = ReasonSlot(reason=None, keyword=None)
        return self


def _merge_keywords(kept: str | None, dropped: str | None) -> str | None:
    """Join a dropped rank's keywords onto the rank that absorbed it.

    Either side may be null -- the lower ranks are optional -- so this is a filtered join rather
    than an f-string, which would otherwise write the text ``None`` into an evidence field. Comma
    separation matches what the field itself asks for, so the merged cell stays one well-formed
    keyword list.
    """
    parts = [part for part in (kept, dropped) if part]
    return ", ".join(parts) if parts else None


class ProductMap(_SchemaModel):
    """The four products, each either analysed or explicitly absent.

    Production declares this object with ``additionalProperties: False`` and **no** ``required``
    list, so the model returns only the products the call mentioned. Strict mode forbids an
    optional property, so all four keys are present here and an unmentioned product carries an
    explicit ``null``. Same contract, expressed in the dialect both backends accept.

    Field order follows production's own declaration order.
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
    """The retention analysis the model returns for one call.

    Declaration order is generation order: the transcript first, then the per-product analysis,
    then the two call-level fields that are conditioned on it.
    """

    transcript: str = Field(description=TRANSCRIPT_DESCRIPTION)
    product: ProductMap
    call_event_detection: EventCategory | None = Field(
        description=(
            "The event that prompted this call. null when the call points to none of the "
            "categories."
        )
    )
    recommendation: str | None = Field(
        description=(
            "How to keep this client loyal to the brand. null when the call gives nothing "
            "to recommend on."
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


__all__ = [
    "Area",
    "EventCategory",
    "IssueTypeCategory",
    "ModelResponse",
    "NetworkIssue",
    "ProductAnalysis",
    "ProductCategory",
    "ProductMap",
    "ReasonCategory",
    "ReasonSlot",
    "RetentionOutcomeCategory",
    "SchemaHelper",
    "TRANSCRIPT_DESCRIPTION",
]
