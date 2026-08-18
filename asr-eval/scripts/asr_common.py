"""Shared contract for the ASR audio eval set.

Everything in this module is reverse-engineered from the production voice pipeline in
``production-reference/sentiment-voice-analysis-develop/``. Each constant carries the
production file and line that fixes it, because the value of this eval set is that the
audio it produces is the *same shape* production hands to a model. A constant changed
here without a matching production citation makes the set unrepresentative silently.

Production path, for the reader who has not opened it:

    SharePoint (Verint call recordings)
      -> QAUploadVoiceTask         .wav only, uploaded as mime audio/wav
      -> GCS  sentiment_qa/input/YYYYMM/YYYYMMDD/{PRODUCT}/*.wav
      -> QAPrepPayloadTask         builds a Vertex batch payload, one fileData part per call
      -> Gemini 2.5 Flash          audio in, QA JSON out, in ONE call
      -> QAGetBatchResultTask      parses the filename back into call metadata

The ASR track exists because that one call is what a text-only candidate cannot do
(``.env.example:27-29``): production sends audio, the candidate cannot receive it, so a
separate ASR step and a transcript artifact have to exist that do not exist today. This
eval set measures that separate ASR step.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from pathlib import Path

# --------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------

# ROOT defaults to `asr-eval/` and is overridable with ASR_EVAL_ROOT.
#
# The override exists so a new pack can be built WITHOUT overwriting a frozen one. The
# twenty-call set under `asr-eval/` is the corpus every published voice number was measured
# on; its .wav files were rendered from the dialogue JSON sitting next to them, so
# regenerating those dialogues in place would silently leave audio that no longer matches
# its own reference transcript -- every CER after that point would be measuring drift.
#
# This mirrors how the text side already handles it: `retention_challenge_v1` was added as a
# new pack id rather than as more items inside `retention_v3`, precisely so a result on one
# can never be mistaken for a result on the other.
ROOT = Path(os.environ.get("ASR_EVAL_ROOT") or Path(__file__).resolve().parents[1])
DIALOGUE_DIR = ROOT / "dialogues"
GROUND_TRUTH_DIR = ROOT / "ground-truth"
AUDIO_DIR = ROOT / "audio"
MASTER_DIR = ROOT / "masters"
REPORT_DIR = ROOT / "reports"
CACHE_DIR = ROOT / "cache"
MANIFEST = ROOT / "manifest.json"

# --------------------------------------------------------------------------------------
# The delivered audio format
# --------------------------------------------------------------------------------------
# production-reference/.../tasks/sentiment_qa/prep_payload_task.py:207-211
#     mapping_file_type = {".txt": "text/plain", ".wav": "audio/wav", ".jsonl": ...}
# and tasks/sentiment_telesale/upload_voice_task.py:352
#     if file_extension and file_extension not in [".wav"]: continue
#
# `.wav` is not a preference in production, it is the only audio extension that survives
# the upload filter and the only one with a mime mapping. An eval set delivered as mp3 or
# flac would never have entered the pipeline it claims to represent.
AUDIO_EXT = ".wav"
AUDIO_MIME = "audio/wav"

# Verint is a call-recording platform; its recordings are narrowband telephony. Production
# does not restate the sample rate anywhere -- it passes the file through untouched -- so
# 8 kHz mono is an inference from the source system, not a citation, and is recorded as
# such in README.md under "What is inferred rather than cited".
DELIVERY_SAMPLE_RATE = 8000
DELIVERY_CHANNELS = 1
DELIVERY_SUBTYPE = "PCM_16"

# The master is kept wideband so a different delivery profile can be re-derived later
# without re-spending TTS calls.
MASTER_SAMPLE_RATE = 24000

# --------------------------------------------------------------------------------------
# The filename contract
# --------------------------------------------------------------------------------------
# This is the load-bearing part. Production parses call metadata straight out of the
# filename by positional split on "_", in three separate places that must agree:
#
#   tasks/sentiment_qa/get_batch_result_task.py:302-319   (the full field map)
#   tasks/sentiment_qa/fact_check_task.py:754-767          (the same map, second copy)
#   tasks/sentiment_qa/prep_payload_task.py:328            (call_direction, index 9)
#
# Index -> field, read off get_batch_result_task.py:306-318:
#
#   0  call_id
#   1  phone_number
#   2  call_time        HHMMSS
#   3  agent_id
#   4  first_name       <- note: index 4..5 are JOINED with " " and .capitalize()'d
#   5  last_name
#   6  provider
#   7  record_date      YYYYMMDD, validated with ^\d{8}$ at :287
#   8  duration         seconds, as a bare integer string
#   9  call_direction   IN | OUT
#
# Worked example. The SHAPE is production's own test fixture at
# tests/test_tasks/sentiment_qa/test_user_playground_task.py:363; the phone number is
# swapped for one from this repository's sanctioned synthetic block, because the phone's
# value is irrelevant to a positional example and CLAUDE.md keeps non-sanctioned numbers
# out of examples and fixtures.
#
#   1111_0810000301_120000_A001_jane_doe_D_20250115_60_IN.wav
#    |        |        |     |    |   |  |     |     |  |
#    0        1        2     3    4   5  6     7     8  9
#
# Two traps this encodes, both of which would silently corrupt metadata if ignored:
#
#   (1) The agent name occupies TWO fields (4 and 5), joined with " " and capitalised at
#       get_batch_result_task.py:311-314. A name containing an underscore, or a
#       single-token name, shifts every later index and production reads the wrong
#       record_date and the wrong call_direction. Names in this set are strictly two
#       underscore-free tokens.
#   (2) prep_payload_task.py:330-338 RAISES on a call_direction that is not exactly
#       "IN" or "OUT" (the raise itself is at :335). A file misnamed here does not
#       degrade, it aborts its payload.
# The ten tokens in positional order. This had NINE entries in the first version -- it
# omitted agent_id (index 3) while FILENAME_TOKEN_COUNT below said 10 and CallMeta
# carried 10 fields, so the module disagreed with itself about its own contract in the
# one constant a reader would check first.
FILENAME_FIELDS = (
    "call_id",
    "phone_number",
    "call_time",
    "agent_id",
    "first_name",
    "last_name",
    "provider",
    "record_date",
    "duration",
    "call_direction",
)

# The same thing as an index map. Redundant on purpose: the tuple gives order at a
# glance, the dict gives a name -> index lookup, and test_contract.py asserts they agree
# so the redundancy cannot silently rot the way it just did.
FILENAME_POSITIONS = {
    "call_id": 0,
    "phone_number": 1,
    "call_time": 2,
    "agent_id": 3,
    "first_name": 4,
    "last_name": 5,
    "provider": 6,
    "record_date": 7,
    "duration": 8,
    "call_direction": 9,
}
FILENAME_TOKEN_COUNT = 10

VALID_DIRECTIONS = ("IN", "OUT")

# production-reference/.../get_batch_result_task.py:287 -- record_date must match this or
# production falls back to the path (:288-293), then to DEFAULT_RECORD_DATE "99991231"
# (:294-300).
RECORD_DATE_RE = re.compile(r"^\d{8}$")
CALL_TIME_RE = re.compile(r"^\d{6}$")

# --------------------------------------------------------------------------------------
# Synthetic phone numbers
# --------------------------------------------------------------------------------------
# CLAUDE.md fixes the sanctioned synthetic block as ^0810000[0-9]{3}$ and records which
# sub-ranges are already spent, measured 2026-08-12:
#
#   0810000000-0810000099   retention_v1, retention_v2, the four block_* fixtures
#   0810000101-0810000138   retention_v3 phase two
#   0810000201-0810000250   retention_challenge_v1
#
# This set takes 0810000301-0810000438: inside the sanctioned block, clear of all three
# spent ranges and clear of the gaps beside them, one number per call. Taking a *new*
# sub-range rather than reusing a spent one keeps the "which pack owns which number"
# audit in CLAUDE.md answerable by inspection.
#
# WIDENED 2026-08-18, from 320 to 438, as a reviewed change to a data-safety control.
#
# What changed and why: the set grew from 20 calls to 138, and the reservation held exactly
# 20 filename numbers. The ceiling below is the thing that refused, deliberately and by
# design -- see the raise in phone_for_index. This is that review, not a bypass of it.
#
#   filenames      0810000301-0810000438   138 numbers, one per call
#   spoken pool    0810000439-0810000576   138 more, the second in-call number
#   TOTAL CLAIMED  0810000301-0810000576   276 numbers
#
# Three facts that make the widening safe, each independently checkable:
#
#   * It is a strict superset of the old range, so no committed value moved. Every number
#     the 20-call set used still means what it meant.
#   * The claimed range is contiguous and entirely unspent. Above 0810000340 nothing in
#     either pack tree had claimed anything, leaving 660 free; 276 fits with 384 to spare.
#   * It stays wholly inside `^0810000[0-9]{3}$`, the block src/evalgen/testsets.py:135
#     enforces. Nothing here reaches outside the sanctioned thousand.
#
# The census in CLAUDE.md moves from 224 in use / 776 free to 464 / 536, and is re-measured
# over BOTH pack trees by tests/test_phone_block_allocation.py rather than hand-edited --
# that test exists because an earlier count scanned only one tree and missed 36 numbers.
PHONE_BLOCK_RE = re.compile(r"^0810000\d{3}$")
ASR_PHONE_FIRST = 301
ASR_PHONE_LAST = 438


def phone_for_index(idx: int) -> str:
    """Phone number for call index `idx` (0-based), inside the sanctioned block."""
    n = ASR_PHONE_FIRST + idx
    if n > ASR_PHONE_LAST:
        raise ValueError(
            f"call index {idx} maps to 0810000{n:03d}, past the {ASR_PHONE_LAST} this set "
            f"reserved. Widening the reservation is a reviewed change to a data-safety "
            f"control (CLAUDE.md), not a convenience."
        )
    return f"0810000{n:03d}"


# Customer-facing numbers that are *spoken inside* a call (a callback number, a number
# being ported). These must also stay in the block, so they are drawn from the same
# reservation rather than invented.
def spoken_phone_pool(idx: int) -> list[str]:
    """Two extra in-block numbers a call may mention, derived from the call index."""
    base = ASR_PHONE_FIRST + idx
    return [f"0810000{base:03d}", f"0810000{ASR_PHONE_LAST + 1 + idx:03d}"]


# --------------------------------------------------------------------------------------
# Filename build / parse
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CallMeta:
    """The ten fields production reads back out of a filename."""

    call_id: str
    phone_number: str
    call_time: str
    agent_id: str
    first_name: str
    last_name: str
    provider: str
    record_date: str
    duration: int
    call_direction: str

    def filename(self) -> str:
        return (
            f"{self.call_id}_{self.phone_number}_{self.call_time}_{self.agent_id}_"
            f"{self.first_name}_{self.last_name}_{self.provider}_{self.record_date}_"
            f"{self.duration}_{self.call_direction}{AUDIO_EXT}"
        )

    def validate(self) -> list[str]:
        """Return a list of contract violations; empty means production would parse it."""
        errs: list[str] = []
        for name in ("call_id", "phone_number", "call_time", "agent_id", "first_name",
                     "last_name", "provider", "record_date", "call_direction"):
            val = getattr(self, name)
            if "_" in str(val):
                errs.append(f"{name}={val!r} contains '_', which shifts every later index")
            if not str(val):
                errs.append(f"{name} is empty, which collapses a positional field")
        if not PHONE_BLOCK_RE.match(self.phone_number):
            errs.append(
                f"phone_number={self.phone_number!r} is outside the sanctioned synthetic "
                f"block ^0810000[0-9]{{3}}$"
            )
        if not RECORD_DATE_RE.match(self.record_date):
            errs.append(f"record_date={self.record_date!r} fails production's ^\\d{{8}}$")
        if not CALL_TIME_RE.match(self.call_time):
            errs.append(f"call_time={self.call_time!r} is not HHMMSS")
        if self.call_direction not in VALID_DIRECTIONS:
            errs.append(
                f"call_direction={self.call_direction!r} is not IN/OUT; "
                f"prep_payload_task.py:335 raises on this rather than degrading"
            )
        if self.duration <= 0:
            errs.append(f"duration={self.duration} must be a positive whole second count")
        return errs


def parse_filename(name: str) -> dict[str, str | None]:
    """Re-implementation of production's positional parse, used to check our own output.

    Mirrors get_batch_result_task.py:302-319 including its quirks: the two name tokens are
    joined and capitalised (:311-314), and every field is a best-effort positional lookup
    that yields None rather than raising.
    """
    stem = Path(name).stem
    parts = stem.split("_")

    def at(i: int) -> str | None:
        return parts[i] if 0 <= i < len(parts) else None

    record_date = at(7)
    if not record_date or not RECORD_DATE_RE.match(record_date):
        record_date = None
    first = at(4) or ""
    return {
        "file_name": stem,
        "file_ext": Path(name).suffix,
        "call_id": at(0),
        "phone_number": at(1),
        "call_time": at(2),
        "agent_id": at(3),
        "first_name": first.capitalize() or None,
        "last_name": (at(5) or "").capitalize() or None,
        "provider": at(6),
        "record_date": record_date,
        "duration": at(8),
        "call_direction": at(9),
    }


# --------------------------------------------------------------------------------------
# Mechanism families
# --------------------------------------------------------------------------------------
# retention_v3 organises its items into named mechanism families so a report can say WHICH
# kind of input an arm fails on and not only how often it fails (DEVLOG.md, the
# `MechanismRow` entry). The families below are the audio analogue. Seven of the ten are
# the acoustic cause of an artifact class that ASR-EXPECTATION.md already enumerates as
# *text*; that mapping is stated per family so a failure here can be traced to a decision
# that was already argued there.

FAMILIES: dict[str, dict[str, str]] = {
    "clean_baseline": {
        "purpose": "Control. Clear speech, moderate pace, good line. If an arm fails here "
                   "the failure is the model, not the channel.",
        "maps_to": "-- control --",
    },
    "telephony_noise": {
        "purpose": "Low SNR: line hiss, hum, crackle. The everyday Verint condition.",
        "maps_to": "RET-119 mid-word truncation, RET-113 tone-mark loss",
    },
    "code_switch": {
        "purpose": "Dense Thai<->English telecom vocabulary inside single sentences.",
        "maps_to": "RET-116 proper noun mangled",
    },
    "numeric_dense": {
        "purpose": "Phone numbers, amounts, dates, package IDs read aloud digit by digit. "
                   "This is where an ASR error costs money downstream.",
        "maps_to": "RET-115 Thai digits",
    },
    "proper_nouns": {
        "purpose": "Brand and product names: TrueMove H, dtac, TrueVisions, TrueID.",
        "maps_to": "RET-116 proper noun mangled",
    },
    "disfluency": {
        "purpose": "Stutters, fillers, false starts, self-repair mid-sentence.",
        "maps_to": "RET-118 stutter",
    },
    "overlap_crosstalk": {
        "purpose": "Both parties speaking at once, interruptions, back-channels.",
        "maps_to": "RET-120 speaker label leaked (diarisation failure surface)",
    },
    "long_context": {
        "purpose": "9-10 minute calls. retention_v3 found the incumbent degrading at 10x "
                   "dilation while other arms only went FLAKY; this is the audio probe of "
                   "the same question.",
        "maps_to": "long_context family in retention_v3",
    },
    "far_field_low_gain": {
        "purpose": "Speakerphone / distant mic: low level, room reverb, high noise floor.",
        "maps_to": "RET-119 mid-word truncation",
    },
    "hold_ivr": {
        "purpose": "Hold music, IVR prompts and dead air interleaved with speech. Tests "
                   "whether an arm hallucinates words into non-speech.",
        "maps_to": "-- insertion / hallucination probe --",
    },
}

SCENARIOS = (
    "retention",
    "downsell",
    "mnp",
    "billing_dispute",
    "net_slow",
    "coverage_issue",
    "sim_replace",
    "payment_arrange",
    "device_promo",
    "telesale_offer",
)

# prep_payload_task.py:330-337 branches the prompt on IN vs OUT, so the set must contain
# both or half of production's prompt surface is never exercised.
OUTBOUND_SCENARIOS = frozenset({"device_promo", "telesale_offer"})


# --------------------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------------------
# An entity is a span whose *value* survives into a downstream field. WER treats a wrong
# digit in a phone number exactly like a wrong particle; the QA pipeline does not. These
# are scored separately for that reason.

ENTITY_TYPES = (
    "phone",       # 10-digit mobile number, spoken digit by digit
    "amount",      # THB
    "date",        # a calendar date
    "package",     # product/package name
    "speed",       # Mbps
    "months",      # contract length
    "id",          # ticket / case / SIM id
)


@dataclass
class Entity:
    type: str
    value: str      # canonical, machine-comparable form
    spoken: str     # exactly the characters that appear in the reference transcript
    turn_idx: int = -1

    def __post_init__(self) -> None:
        if self.type not in ENTITY_TYPES:
            raise ValueError(f"unknown entity type {self.type!r}")


@dataclass
class Turn:
    idx: int
    speaker: str            # "agent" | "customer" | "ivr"
    text: str               # verbatim Thai, exactly what is synthesised and expected back
    gap_before_s: float = 0.35
    rate: str = "+0%"
    pitch: str = "+0Hz"
    overlap_s: float = 0.0  # >0 pulls this turn EARLIER, over the previous one
    entities: list[Entity] = field(default_factory=list)
    kind: str = "speech"    # "speech" | "ivr" | "hold"
    # Only meaningful when kind == "hold". Declared as a real field rather than stashed on
    # __dict__ because `dataclasses.asdict` serialises declared fields only -- an attribute
    # set outside the declaration survives in memory and vanishes on the way to JSON, so
    # every hold segment would silently become the default length after a round trip.
    hold_s: float = 0.0


@dataclass
class Dialogue:
    item_id: str
    family: str
    scenario: str
    call_direction: str
    meta: CallMeta
    turns: list[Turn]
    agent_voice: str
    customer_voice: str
    target_duration_s: int
    seed: int
    notes: str = ""

    def reference_transcript(self) -> str:
        """The scored reference: speech turns only, in order, one line each.

        IVR and hold segments are excluded deliberately -- they are machine audio, not the
        conversation, and an ASR arm is not wrong to omit or include them. They are
        recorded in `turns` so the hold_ivr family can score insertions against known
        non-speech spans instead.
        """
        return "\n".join(t.text for t in self.turns if t.kind == "speech")

    def flat_reference(self) -> str:
        """Single-line reference, for CER and for tokenised WER."""
        return " ".join(t.text for t in self.turns if t.kind == "speech")

    def all_entities(self) -> list[Entity]:
        out: list[Entity] = []
        for t in self.turns:
            for e in t.entities:
                out.append(Entity(e.type, e.value, e.spoken, t.idx))
        return out

    def to_json(self) -> dict:
        d = asdict(self)
        d["meta"] = asdict(self.meta)
        d["filename"] = self.meta.filename()
        d["reference_transcript"] = self.reference_transcript()
        return d


# --------------------------------------------------------------------------------------
# Thai text normalisation for scoring
# --------------------------------------------------------------------------------------
# Deliberately NARROW, and narrow for the reason ASR-EXPECTATION.md gives at :52-59:
# "Forgive where the mapping is unambiguous. Do not forgive where the model had to guess."
# Only lossless, one-to-one repairs live here. Anything requiring a choice between
# candidates is left to fail, because a rule that forgives inference cannot tell a correct
# inference from a fabrication.

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")

# The two controls from ASR-EXPECTATION.md that must always normalise (RET-121, RET-122).
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"), None)


def normalise_thai(s: str) -> str:
    """Lossless normalisation only. See the note above before adding to this."""
    s = unicodedata.normalize("NFC", s)
    s = s.translate(_ZERO_WIDTH)          # RET-122 control: zero-width chars carry no content
    s = s.replace("เเ", "แ")  # RET-121 control: two SARA E -> SARA AE
    s = s.translate(_THAI_DIGITS)         # RET-115: total, unambiguous, cannot touch non-digits
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def digits_only(s: str) -> str:
    return re.sub(r"\D", "", normalise_thai(s))
