from __future__ import annotations

from typing import Any, Literal

from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

TRANSCRIPT_DESCRIPTION = """ถอดบทสนทนาจากชิ้นเสียง (audio chunk) ที่ได้รับ ตามที่ได้ยินจริง โดยแยกผู้พูดออกเป็น 2 บทบาท คือ `Agent` (พนักงาน call center) และ `Customer` (ลูกค้า)

**0. Chunk context (ชิ้นเสียงนี้เป็นส่วนหนึ่งของสายสนทนา ไม่ใช่ทั้งสาย):**
- เสียงที่ได้รับถูกตัดมาจากสายสนทนาที่ยาวกว่า อาจเริ่มหรือจบกลางประโยค ให้ถอดเฉพาะเสียงที่ได้ยินจริงในชิ้นนี้ ห้ามแต่งเติมให้ประโยคสมบูรณ์ และห้ามเดาเนื้อหาส่วนที่ไม่ได้ยิน
- ชิ้นเสียงอาจมีผู้พูดเพียงฝ่ายเดียวตลอดทั้งชิ้น หรือไม่มีบทสนทนาเลยก็ได้ (เช่น เป็นช่วงเงียบหรือเสียงรอสายทั้งชิ้น)
- อาจไม่มีคำทักทายเปิดสายหรือคำกล่าวปิดสาย ห้ามใช้ตำแหน่งในชิ้นเสียงเป็นตัวตัดสินบทบาทผู้พูด

**1. Speaker identification (ต้องระบุจากน้ำเสียงและเนื้อหา ไม่ใช่จากลำดับการพูดหรือช่วงเงียบ):**
- ห้ามสมมติว่าผู้ที่พูดประโยคแรกคือ `Agent` เสมอไป ต้องตัดสินบทบาทของแต่ละฝ่ายจากเนื้อหา บทบาท และพฤติกรรมการพูดที่ปรากฏจริงในไฟล์เสียง แล้วจึงใช้บทบาทนั้นตลอดทั้งชิ้นเสียง
- ในสายมักมีช่วงเงียบ (dead air) เช่น พนักงานกำลังตรวจสอบข้อมูลในระบบ ลูกค้าหยุดคิด หรือสายเงียบชั่วคราว **ห้ามสมมติว่าเสียงที่พูดถัดจากช่วงเงียบเป็นของอีกฝ่ายเสมอไป** ผู้พูดคนเดิมอาจพูดต่อเองก็ได้ ให้ยึดจากลักษณะน้ำเสียงของผู้พูดและเนื้อหาที่พูดเป็นหลัก ไม่ใช่จากการมีช่วงเงียบคั่น
- หากผู้พูดคนเดิมพูดต่อหลังช่วงเงียบ ให้รวมไว้ในเทิร์นเดิม และห้ามใส่หมายเหตุช่วงเงียบ เช่น `[เงียบ]` หรือ `[dead air]` ลงในผลลัพธ์
- สัญญาณของ `Agent`: แจ้งชื่อบริษัท/ชื่อตนเองว่าเป็นพนักงาน, ขอยืนยันตัวตนลูกค้า (ชื่อ-นามสกุล / เลขบัตรประชาชน / เบอร์ที่ใช้บริการ), เสนอความช่วยเหลือ, ขอถือสายรอ (hold), อธิบายเงื่อนไข แพ็กเกจ ค่าบริการ หรือขั้นตอนดำเนินการ, เสนอขายหรือเสนอโปรโมชัน, ใช้ถ้อยคำสุภาพตามสคริปต์บริการ, เป็นฝ่ายควบคุมลำดับของบทสนทนาและกล่าวปิดการสนทนา
- สัญญาณของ `Customer`: แจ้งปัญหาหรือความต้องการ, ตอบคำถามยืนยันตัวตน, ให้ข้อมูลส่วนตัวของตนเอง, สอบถามสิทธิ์ ค่าบริการ หรือผลการดำเนินการ, แสดงอารมณ์ ความกังวล หรือความไม่พอใจ, ขอให้ดำเนินการแทน
- ใช้เกณฑ์เดียวกันไม่ว่าสายต้นทางเป็น inbound (ลูกค้าโทรเข้า) หรือ outbound (พนักงานโทรออก) ผู้ที่เริ่มพูดก่อนในชิ้นเสียงเป็นฝ่ายใดก็ได้
- หากมีการโอนสายและมีพนักงานคนที่สองเข้ามาในสาย ให้ใช้ป้ายว่า `Agent` เหมือนกัน (ห้ามแยกเป็น Agent 1 / Agent 2)
- ข้ามเสียงที่ไม่ใช่บทสนทนาระหว่างสองฝ่าย เช่น เสียงระบบ IVR, เสียงประกาศอัตโนมัติ, เสียงเพลงรอสาย

**2. Fidelity (ห้ามเปลี่ยนแปลงถ้อยคำ):**
- ถอดตามที่ได้ยินจริงทุกคำ **ห้ามแปลภาษา ห้ามสรุป ห้ามย่อความ ห้ามเรียบเรียงใหม่ ห้ามแก้ไขไวยากรณ์หรือคำผิด**
- ใช้ภาษาเดียวกับที่พูดในไฟล์เสียงเสมอ พูดไทยให้ถอดเป็นไทย พูดอังกฤษให้ถอดเป็นอังกฤษ พูดปนกัน (code-switching) ให้คงไว้ตามที่พูด
- คงคำติดปาก คำซ้ำ คำลงท้าย และประโยคที่พูดไม่จบไว้ตามเดิม (เช่น "เอ่อ", "อ่า", "ค่ะ", "ครับ", "นะคะ")
- ห้ามเติมข้อความที่ไม่ได้พูด และห้ามเดา หากช่วงใดฟังไม่ชัดหรือไม่ได้ยิน ให้ใส่ `[ไม่ชัดเจน]` เฉพาะช่วงนั้น
- ถอดข้อมูลส่วนบุคคลที่พูดในสาย (ชื่อ เลขบัตรประชาชน เบอร์โทรศัพท์ ที่อยู่ เลขที่บัญชี) ตามที่ได้ยินจริง **ห้ามปิดบัง ห้ามแทนที่ด้วย placeholder และห้ามตัดออก**

**3. Output format:**
- หนึ่งเทิร์นต่อหนึ่งบรรทัด รูปแบบคือ `Agent: <ข้อความที่พูด>` หรือ `Customer: <ข้อความที่พูด>` ขึ้นบรรทัดใหม่ทุกครั้งที่เปลี่ยนผู้พูด **ห้ามเว้นบรรทัดว่างระหว่างเทิร์น**
- ใช้ป้ายชื่อเป็นภาษาอังกฤษว่า `Agent` และ `Customer` เท่านั้น แม้เนื้อหาที่พูดจะเป็นภาษาไทย
- รวมประโยคที่ผู้พูดคนเดียวกันพูดต่อเนื่องกันไว้ในเทิร์นเดียว และเรียงลำดับเทิร์นตามเวลาจริงในไฟล์เสียง
- ห้ามใส่ timestamp, เลขลำดับเทิร์น, หัวข้อ, markdown, code fence หรือคำอธิบายใด ๆ เพิ่มเติม
- หากชิ้นเสียงไม่มีบทสนทนา (เงียบทั้งชิ้น หรือมีแต่เสียงที่ไม่ใช่บทสนทนา) ให้ตอบเป็นข้อความว่าง
- ตัวอย่างรูปแบบผลลัพธ์ (ผู้เริ่มพูดเป็นฝ่ายใดก็ได้ ขึ้นกับไฟล์เสียงจริง):
Agent: <ข้อความที่พูดจริง>
Customer: <ข้อความที่พูดจริง>
Agent: <ข้อความที่พูดจริง>
"""


def _drop_docstring_description(schema: dict[str, Any]) -> None:
    """Keep class docstrings out of the schema handed to the model.

    Pydantic promotes a model's docstring to the object's ``description``; only
    ``Field(description=...)`` is prompt text.
    """
    schema.pop("description", None)


class ChunkResponse(BaseModel):
    """Per-chunk analysis of one audio segment cut from a longer call.

    This is the only stage of the pipeline that hears audio — downstream
    aggregation works from text alone — so the acoustic fields exist to write
    down what a transcript cannot carry (tone, speed).

    Property order drives generation order: ``transcript`` first, then the
    acoustic observations, then the judgments, so each judgment is conditioned
    on evidence the model already committed to rather than on the audio alone.
    """

    model_config = ConfigDict(
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
        json_schema_extra=_drop_docstring_description,
    )

    transcript: str = Field(description=TRANSCRIPT_DESCRIPTION)
    customer_speech_rate: Literal["slow", "normal", "fast", "varied", "none"] = Field(
        description=(
            "The customer's speaking speed as heard in this chunk, judged against normal "
            "Thai conversational pace: 'slow', 'normal', 'fast', or 'varied' if it changes "
            "notably within the chunk. Judge from the audio, not the wording. Answer 'none' "
            "if no customer speech is audible in this chunk."
        )
    )
    customer_voice_tone: str = Field(
        description=(
            "A short English phrase describing the customer's voice as heard in this chunk "
            "— tone, volume, and stability (e.g. 'calm and steady', 'raised voice, tense', "
            "'trembling, hesitant'). Describe only what is audible in the audio; do not "
            "infer from wording alone. Answer 'none' if no customer speech is audible in "
            "this chunk."
        )
    )
    customer_emotional: Literal[
        "angry",
        "frustrated",
        "annoyed",
        "confused",
        "worried",
        "disappointed",
        "neutral",
        "calm",
        "satisfied",
        "happy",
        "none",
    ] = Field(
        description=(
            "The customer's emotional state in this chunk, inferred from voice tone, speech "
            "patterns, speaking speed, and what is said. Answer with exactly one word from "
            "the allowed values. Use 'neutral' when no clear emotion is audible, and 'none' "
            "if no customer speech is audible in this chunk."
        )
    )
    customer_sentiment: Literal["positive", "neutral", "negative"] = Field(
        description=(
            "The customer's overall sentiment in this chunk, inferred from voice tone, "
            "speech patterns, speaking speed, and content: 'positive' = clear satisfaction, "
            "gratitude, or a positive tone; 'negative' = clear dissatisfaction, "
            "frustration, anger, blame, or worry; 'neutral' = ordinary tone, routine "
            "enquiry or information exchange, or no clear signal either way. Judge from "
            "this chunk only. Answer 'neutral' if no customer speech is audible."
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
    """Renders :class:`ChunkResponse` for each backend's schema dialect.

    Vertex and OpenAI want contradictory schema forms, so there is one renderer per target.
    For online google-genai calls, pass the class straight to ``GenerateContentConfig`` and
    read the typed result off ``response.parsed`` instead.
    """

    @staticmethod
    def vertex_schema() -> types.Schema:
        """Convert :class:`ChunkResponse` to Vertex's OpenAPI-subset ``Schema``.

        ``from_json_schema`` inlines ``$defs`` (required: Vertex ingests batch JSONL via a
        BigQuery load, which rejects ``$``-prefixed keys), uppercases types, and folds
        ``anyOf: [X, null]`` into ``nullable: True`` -- hand-rolling that fold silently breaks
        nullable fields. ``raise_error_on_unsupported_field`` prevents silent field drops.
        """
        return types.Schema.from_json_schema(
            json_schema=types.JSONSchema.model_validate(ChunkResponse.model_json_schema()),
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
        schema = ChunkResponse.model_json_schema(union_format="primitive_type_array")
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "model_response",
                "strict": True,
                "schema": _forbid_additional_properties(schema),
            },
        }


__all__ = ["ChunkResponse", "SchemaHelper"]
