"""Compose the 20 call dialogues and their ground truth.

GROUND TRUTH IS AUTHORED, NOT TRANSCRIBED. This is the whole point of the design and it
is worth being explicit about, because it is the opposite of how ASR sets are usually
built:

    normal:  record real audio  ->  a human transcribes it  ->  that transcript is truth
    here:    author the text    ->  synthesise audio from it ->  the text is truth

The normal route puts a human transcriber between the audio and the reference, and that
human has an error rate of their own. Here the reference is exact by construction: the
audio was generated FROM the text, so the text is not an estimate of what was said, it is
what was said. That removes reference error entirely as a term in every WER we report.

What it costs, stated so no one has to discover it later: the audio is synthetic, so it
has none of the acoustic messiness of a real Verint recording that the telephony chain in
synthesize.py does not explicitly model. A WER measured here is NOT a production WER
estimate. It is a controlled comparison -- arm against arm on identical audio, and one arm
across the ten mechanism families. That is the comparison this repository exists to make.

Run:
    python asr-eval/scripts/compose_dialogues.py
"""

from __future__ import annotations

import csv
import json
import os
import random
from collections import deque
from dataclasses import asdict
from datetime import date, timedelta

import asr_common as C
import business_labels as B
import thai_corpus as T
import thai_num as N

# Thai speech rate, chars/sec, measured on ASSEMBLED audio -- not on raw TTS clips.
#
# The first value here was 13.6, the mean of the two voices measured on raw edge-tts
# output. It produced a 167.7 s file against a 215 s target, 22% short, and one file below
# the 180 s floor the set promises. The cause is that synthesize.trim_silence strips the
# leading and trailing padding the TTS service adds to every clip, so the raw measurement
# was timing several hundred ms of silence per turn as if it were speech.
#
# 17.2 is re-derived from a real render of ASR-001: 2,460 speech chars occupying 142.5 s of
# speech in the assembled timeline. Trust the assembled number, not the clip number.
CHARS_PER_SEC = 17.2

# synthesize.render() adds a 0.6 s lead-in and a 0.8 s tail that the composer never sees.
LEAD_TAIL_S = 1.4

# How many recently-used templates the composer refuses to reuse. 22 is roughly ten
# exchanges, far enough apart that a repeat reads as a caller genuinely circling back
# rather than as the generator running out of things to say. Raised from 14 after the
# first build put the same agent sentence four times in one 9-minute call; at 22, unique
# substantive lines run 100% on the short calls and 72-80% on the two longest, where an
# agent really does repeat themselves.
RECENT_WINDOW = 22

# Targets, deliberately held away from the 180 s / 600 s edges so a calibration error
# cannot push a file out of the band the set promises. Spread across the whole range and
# paired with families so that duration is not confounded with family.
# The two `long_context` slots (PLAN indices 7 and 17) take the two longest targets, so
# the family that exists to probe length actually is the longest thing in the set. Getting
# this backwards would have left `long_context` mid-range and the label meaningless.
# The long_context targets are 545/555 rather than 555/575. At 575 the composer's overshoot
# (it adds turns in pairs, then a fixed closing block) landed ASR-018 at 598.2 s -- 1.8 s
# under the ceiling the set promises. That is inside the band by luck, not by design: TTS
# timing varies between runs, so the next render could put it over. 545/555 keeps both
# calls at 9.4-9.7 minutes with ~25 s of margin.
SEED_TARGETS = [
    215, 245, 275, 310, 340, 370, 400, 545, 460, 490,
    230, 260, 295, 325, 355, 385, 415, 555, 430, 445,
]

# Ten families x 2 calls. The second of each pair takes a different scenario and a
# different speaker-gender assignment, so a family effect cannot be a speaker effect.
SEED_PLAN: list[tuple[str, str]] = [
    ("clean_baseline", "retention"),
    ("telephony_noise", "billing_dispute"),
    ("code_switch", "net_slow"),
    ("numeric_dense", "payment_arrange"),
    ("proper_nouns", "device_promo"),
    ("disfluency", "downsell"),
    ("overlap_crosstalk", "mnp"),
    ("long_context", "retention"),
    ("far_field_low_gain", "coverage_issue"),
    ("hold_ivr", "sim_replace"),
    ("clean_baseline", "telesale_offer"),
    ("telephony_noise", "net_slow"),
    ("code_switch", "downsell"),
    ("numeric_dense", "billing_dispute"),
    ("proper_nouns", "telesale_offer"),
    ("disfluency", "coverage_issue"),
    ("overlap_crosstalk", "retention"),
    ("long_context", "billing_dispute"),
    ("far_field_low_gain", "sim_replace"),
    ("hold_ivr", "payment_arrange"),
]

# --------------------------------------------------------------------------------------
# Scaling the design past twenty calls
# --------------------------------------------------------------------------------------
# SET_SIZE is the one number to change. The two literals above are kept as the seed so the
# first twenty calls keep the family, scenario and duration they have always had, and a diff
# between a 20-call and a 138-call build stays readable.
#
# Three invariants the seed encodes, preserved by the extension rather than re-derived:
#
#   * Every family gets a spread of durations. If `telephony_noise` were systematically the
#     shortest family, a CER difference by family would be a CER difference by length.
#   * `long_context` takes the top of the band. It is the family whose entire purpose is
#     length; leaving it mid-range makes the label meaningless.
#   * No family sits on one scenario. Scenario rotates with an offset per family so
#     family x scenario coverage is spread rather than paired.
#
# The band is 180-600 s and the extension stays inside 195-500 for ordinary families. The
# composer overshoots (it adds turns in pairs, then a fixed closing block), which is why the
# seed's long_context targets are 545/555 and not 575 -- at 575 a render landed 1.8 s under
# the ceiling, inside the band by luck rather than design.

# Settable so a 20-call smoke build and the real 138 come from one code path. An env var
# rather than a CLI flag because synthesize.py, validate_audio.py and the tests all have to
# agree on the size and they are separate processes.
SET_SIZE = int(os.environ.get("ASR_SET_SIZE", "20"))

FAMILIES = [
    "clean_baseline", "telephony_noise", "code_switch", "numeric_dense", "proper_nouns",
    "disfluency", "overlap_crosstalk", "long_context", "far_field_low_gain", "hold_ivr",
]

SCENARIOS = [
    "retention", "billing_dispute", "net_slow", "payment_arrange", "device_promo",
    "downsell", "mnp", "coverage_issue", "sim_replace", "telesale_offer",
]

# Scenario weights, out of 100. The seed set gave every scenario two calls, which is right
# for an ASR set -- ten acoustic families over ten call types, nothing favoured. It is wrong
# for a RETENTION set, in a way that showed up as soon as business labels existed:
#
#   * This is a Retention eval. retention/downsell/mnp are the calls the decision is about
#     and they should dominate; a uniform draw made them 30% of the set.
#   * Uniform scenarios distorted the label mix. `telesale_offer` and `device_promo` are
#     outbound sales with no service being retained, so they are mostly `undefined`. At 14
#     calls each they pushed `undefined` to 12% of the set against 3.3% in the hand-labelled
#     text packs, which would have made business accuracy on the two corpora incomparable.
#
# Weighted, retention-shaped calls are ~48% and outbound ~8%, which brings `undefined` back
# near the packs. These are targets for the draw, not quotas; the realised mix is printed.
SCENARIO_WEIGHTS = {
    "retention": 22,
    "downsell": 14,
    "mnp": 12,
    "billing_dispute": 12,
    "net_slow": 10,
    "coverage_issue": 8,
    "payment_arrange": 8,
    "sim_replace": 6,
    "device_promo": 4,
    "telesale_offer": 4,
}

# Ordinary families sweep this range; long_context gets its own, at the top.
DURATION_BAND = (195, 500)
LONG_CONTEXT_BAND = (505, 560)


def build_design(size: int) -> tuple[list[tuple[str, str]], list[int]]:
    """(plan, targets) of length `size`, seeded by the committed twenty."""
    if size < len(SEED_PLAN):
        return SEED_PLAN[:size], SEED_TARGETS[:size]

    plan = list(SEED_PLAN)
    targets = list(SEED_TARGETS)

    # How many more calls each family needs, distributed as evenly as the remainder allows.
    extra = size - len(SEED_PLAN)

    # The weighted scenario supply for the extension, built once and drawn from below.
    # Largest-remainder apportionment so the counts sum to `extra` exactly rather than
    # drifting by a call or two, and interleaved so consecutive draws differ.
    exact = {name: extra * weight / 100 for name, weight in SCENARIO_WEIGHTS.items()}
    counts = {name: int(value) for name, value in exact.items()}
    for name in sorted(exact, key=lambda k: exact[k] - counts[k], reverse=True):
        if sum(counts.values()) >= extra:
            break
        counts[name] += 1
    scenario_queue: list[str] = []
    remaining = dict(counts)
    while sum(remaining.values()):
        for name in SCENARIOS:
            if remaining.get(name):
                scenario_queue.append(name)
                remaining[name] -= 1
    per_family = [extra // len(FAMILIES)] * len(FAMILIES)
    for i in range(extra % len(FAMILIES)):
        per_family[i] += 1

    # Duration sweeps: each family walks its own band so no family clusters at one length.
    for family_i, family in enumerate(FAMILIES):
        count = per_family[family_i]
        if not count:
            continue
        low, high = LONG_CONTEXT_BAND if family == "long_context" else DURATION_BAND
        for k in range(count):
            # Offset the sweep per family so two families never share a duration ladder.
            frac = (k + 0.5 * (family_i % 2)) / max(count, 1)
            target = int(round(low + frac * (high - low)))
            # Scenarios come from a weighted queue rather than a rotation, so the SET hits
            # its intended mix. The queue is consumed with a per-family offset so a family
            # still sees several different scenarios instead of one repeated.
            scenario = scenario_queue[(len(plan) + family_i) % len(scenario_queue)]
            plan.append((family, scenario))
            targets.append(target)

    # Interleave the extension so consecutive call indices are not the same family. The
    # composer alternates speaker gender on idx % 2; leaving families in blocks would pair
    # every family with one gender and reintroduce the confound the seed avoids.
    tail = list(zip(plan[len(SEED_PLAN):], targets[len(SEED_TARGETS):]))
    by_family: dict[str, list] = {}
    for entry in tail:
        by_family.setdefault(entry[0][0], []).append(entry)
    interleaved = []
    while any(by_family.values()):
        for family in FAMILIES:
            bucket = by_family.get(family)
            if bucket:
                interleaved.append(bucket.pop(0))

    plan = plan[:len(SEED_PLAN)] + [entry[0] for entry in interleaved]
    targets = targets[:len(SEED_TARGETS)] + [entry[1] for entry in interleaved]
    return plan, targets


PLAN, TARGETS = build_design(SET_SIZE)

PARTICLES = {
    "f": {"p": "ค่ะ", "pq": "คะ", "self": "ดิฉัน"},
    "m": {"p": "ครับ", "pq": "ครับ", "self": "ผม"},
}
VOICES = {"f": "th-TH-PremwadeeNeural", "m": "th-TH-NiwatNeural"}

BASE_DATE = date(2026, 8, 3)


# Thai polite particles change form after certain preceding particles. The rule is not
# optional politeness -- ค่ะ after นะ is simply wrong, and every Thai speaker writes นะคะ.
#
# This is applied AFTER slot filling rather than being written into each template, because
# the templates carry "{p}" and only know the speaker's gender at render time. Getting it
# wrong bakes an error into the reference transcript: the TTS voice pronounces ค่ะ with a
# falling tone where the audio should carry a high tone, so the audio and the reference are
# both wrong in the same way and nothing downstream can detect it.
PARTICLE_FIXES = (
    ("นะค่ะ", "นะคะ"),
    ("ล่ะค่ะ", "ล่ะคะ"),
    ("สิค่ะ", "สิคะ"),
    ("เหรอค่ะ", "เหรอคะ"),
)


def fix_particles(text: str) -> str:
    for wrong, right in PARTICLE_FIXES:
        text = text.replace(wrong, right)
    return text


def _gender_ok(template: str, gender: str) -> bool:
    """Reject templates whose hardcoded particles contradict the speaker's gender.

    Some lines in thai_corpus are written with a fixed ครับ or ค่ะ because they only read
    naturally that way ("ครับ พูดอยู่ครับ"). Handing one to the wrong voice produces Thai
    that no call-centre agent would say, which would be a realism defect baked into the
    reference transcript itself.
    """
    male_marks = "ครับ" in template
    female_marks = "ค่ะ" in template or "คะ" in template
    if gender == "m" and female_marks:
        return False
    if gender == "f" and male_marks:
        return False
    return True


class Composer:
    def __init__(self, idx: int, family: str, scenario: str, target_s: int) -> None:
        self.idx = idx
        self.family = family
        self.scenario = scenario
        self.target_s = target_s
        self.seed = 20260816 + idx
        self.rng = random.Random(self.seed)

        # Alternate which side gets which voice so family is never confounded with voice.
        self.agent_gender = "f" if idx % 2 == 0 else "m"
        self.cust_gender = "m" if idx % 2 == 0 else "f"

        # Accessors, not direct indexing: the three name lists are 20 long and `[idx]`
        # raised IndexError at call 20, which is what capped the set. agent_name/
        # customer_name return the same values for 0..19 and extend combinatorially past it.
        self.agent_thai, self.agent_latin = T.agent_name(idx)
        self.cust_thai = T.customer_name(idx)

        # --- the business label, drawn BEFORE any text exists ------------------------
        # This is the inversion that makes the audio set scoreable end to end. The old
        # composer flipped a coin mid-dialogue and the outcome was whatever the coin said,
        # so the only way to know a call's label was to read its transcript back. Here the
        # label is an INPUT: the dialogue is rendered to express it, `business.csv` records
        # it, and nothing downstream has to recover it from text. See business_labels.py.
        self.product = B.choose_product(self.rng)
        self.call_result = B.choose_call_result(self.rng, scenario)
        self.main, self.secondary, self.third = B.choose_reasons(self.rng, scenario)
        # Brand follows product rather than being drawn independently: a TrueVisions call
        # labelled `postpaid` would be a row nobody could defend.
        self.brand = T.BRANDS[B.brand_index_for(self.rng, self.product)]

        self.direction = "OUT" if scenario in C.OUTBOUND_SCENARIOS else "IN"

        # --- entity values, fixed up front so the same call always quotes the same ones --
        self.phone = C.phone_for_index(idx)
        self.phone2 = C.spoken_phone_pool(idx)[1]
        # Four money values with a fixed ordering the templates rely on:
        #   amount3 < amount_offer < amount < amount2
        # See the header of thai_corpus.py. A single figure used for all four made an agent
        # answer "I pay 999 and it is too much" with a 999 package and a 999 discount.
        self.amount = self.rng.choice([499, 599, 699, 799, 899, 999])
        self.amount2 = self.amount + self.rng.choice([100, 200, 300, 400])
        self.amount_offer = self.amount - self.rng.choice([100, 150, 200, 250])
        self.amount3 = self.rng.choice([50, 80, 100, 150])
        assert self.amount3 < self.amount_offer < self.amount < self.amount2
        self.package = self.rng.choice(T.PACKAGES)
        self.package2 = self.rng.choice([p for p in T.PACKAGES if p != self.package])
        self.speed_mbps = self.rng.choice([100, 200, 300, 500, 1000])
        self.gb = self.rng.choice([10, 15, 20, 30, 40, 50, 100])
        self.months = self.rng.choice([3, 6, 12, 24])
        self.case_id = f"{self.rng.randint(1000, 9999)}{self.rng.randint(10, 99)}"
        self.d1 = BASE_DATE + timedelta(days=self.rng.randint(1, 25))
        self.d2 = BASE_DATE + timedelta(days=self.rng.randint(26, 60))
        self.hour = self.rng.choice([9, 10, 13, 14, 15, 16])

        self.turns: list[C.Turn] = []
        self.est_s = LEAD_TAIL_S
        self.recent: deque[str] = deque(maxlen=RECENT_WINDOW)

    # -- slot filling -------------------------------------------------------------------

    def _slots(self, speaker: str) -> dict[str, str]:
        g = self.agent_gender if speaker == "agent" else self.cust_gender
        s = dict(PARTICLES[g])
        s.update(
            agent_thai=self.agent_thai,
            cust_thai=self.cust_thai,
            brand=self.brand,
            package=self.package,
            package2=self.package2,
            phone=N.read_digits(self.phone),
            phone2=N.read_digits(self.phone2),
            amount=N.read_baht(self.amount),
            amount2=N.read_baht(self.amount2),
            amount3=N.read_baht(self.amount3),
            amount_offer=N.read_baht(self.amount_offer),
            date=N.read_date(self.d1.year, self.d1.month, self.d1.day),
            date2=N.read_date(self.d2.year, self.d2.month, self.d2.day),
            speed=N.read_speed(self.speed_mbps),
            gb=N.read_gb(self.gb),
            months=N.read_months(self.months),
            case_id=N.read_digits(self.case_id),
            time=N.read_time(self.hour, 0),
        )
        return s

    def _entities_in(self, text: str) -> list[C.Entity]:
        """Attach an entity for every canonical value whose spoken form is in `text`.

        Membership is decided on the rendered string, so an entity can never be claimed for
        a turn that does not actually say it -- the annotation cannot drift from the audio.
        """
        found: list[C.Entity] = []
        candidates = [
            ("phone", self.phone, N.read_digits(self.phone)),
            ("phone", self.phone2, N.read_digits(self.phone2)),
            ("amount", str(self.amount), N.read_baht(self.amount)),
            ("amount", str(self.amount2), N.read_baht(self.amount2)),
            ("amount", str(self.amount3), N.read_baht(self.amount3)),
            ("amount", str(self.amount_offer), N.read_baht(self.amount_offer)),
            ("date", self.d1.isoformat(), N.read_date(self.d1.year, self.d1.month, self.d1.day)),
            ("date", self.d2.isoformat(), N.read_date(self.d2.year, self.d2.month, self.d2.day)),
            ("speed", str(self.speed_mbps), N.read_speed(self.speed_mbps)),
            ("months", str(self.months), N.read_months(self.months)),
            ("id", self.case_id, N.read_digits(self.case_id)),
            ("package", self.package, self.package),
            ("package", self.package2, self.package2),
        ]
        for etype, value, spoken in candidates:
            if spoken and spoken in text:
                found.append(C.Entity(etype, value, spoken))
        return found

    # -- family-specific text transforms ------------------------------------------------

    def _apply_disfluency(self, text: str, speaker: str) -> str:
        """The audio cause of ASR-EXPECTATION.md class 6 (RET-118, stutter).

        The reference transcript keeps the disfluency verbatim. An arm that silently
        cleans it up is not more correct -- it is editing the source, which is exactly the
        distinction ASR-EXPECTATION.md draws at :110-112 for speaker-label leakage.
        """
        r = self.rng
        # Stutter FIRST, so it repeats the real opening word. Doing it after the filler
        # prepend stutters the filler instead ("แบบว่า แบบว่า ..."), which is a different
        # and much less interesting disfluency than repeating a content word.
        if r.random() < 0.30:
            head = text.split(" ")[0]
            if head and len(head) <= 8:
                text = f"{head} {text}"
        if r.random() < 0.45:
            text = f"{r.choice(T.FILLER_SOUNDS)} {text}"
        # Only substantive turns get a self-repair, and never a turn that quotes an entity.
        #
        # The length guard: appending a retraction to a two-syllable back-channel ("ครับ")
        # produces a turn that is all repair and no content.
        #
        # The entity guard is the important one. "เอ๊ะ เดี๋ยวนะ ที่พูดไปเมื่อกี้ไม่ถูก"
        # landing straight after the customer reads out their phone number says the number
        # was wrong -- so the turn now carries two candidate truths and the entity scorer
        # would be measuring an ambiguity we introduced ourselves.
        if len(text) >= 40 and not self._entities_in(text) and r.random() < 0.18:
            text = f"{text} {r.choice(T.SELF_REPAIR)}"
        return text

    def _apply_code_switch(self, text: str, speaker: str) -> str:
        if speaker == "agent" and self.rng.random() < 0.55:
            term = self.rng.choice(T.CODE_SWITCH_TERMS)
            slots = self._slots(speaker)
            text = f"{text} ตรงส่วนของ{term}นะ{slots['p']}"
        elif speaker == "customer" and self.rng.random() < 0.35:
            term = self.rng.choice(T.CODE_SWITCH_TERMS)
            text = f"{text} แล้ว{term}ล่ะ"
        return text

    def _apply_proper_nouns(self, text: str, speaker: str) -> str:
        if self.rng.random() < 0.45:
            slots = self._slots(speaker)
            noun = self.rng.choice(T.BRANDS + T.PACKAGES)
            text = f"{text} ในส่วนของ{noun}นะ{slots['p']}"
        return text

    # -- turn construction --------------------------------------------------------------

    def business_label(self) -> B.BusinessLabel:
        """The row this call contributes to ground truth.

        `call_id` and `phone_number` must match the values the composer already put in
        `CallMeta`, because that is what the scorer joins on -- see metrics.outer_join,
        which merges on (call_id, phone, product).
        """
        return B.BusinessLabel(
            call_id=f"{7100 + self.idx}",
            phone_number=self.phone,
            product=self.product,
            call_result=self.call_result,
            main=self.main,
            secondary=self.secondary,
            third=self.third,
        )

    def add(self, speaker: str, pool: list[str], kind: str = "speech") -> None:
        g = self.agent_gender if speaker == "agent" else self.cust_gender
        usable = [t for t in pool if _gender_ok(t, g)] or pool

        # Avoid anything used in the last RECENT_WINDOW picks. Sampling small pools with
        # replacement put the same sentence two turns apart in the first build, which reads
        # as a generator artefact rather than a call and inflates any n-gram-ish metric.
        fresh = [t for t in usable if t not in self.recent]
        template = self.rng.choice(fresh or usable)
        self.recent.append(template)

        text = template.format(**self._slots(speaker))

        if kind == "speech":
            if self.family == "disfluency":
                text = self._apply_disfluency(text, speaker)
            elif self.family == "code_switch":
                text = self._apply_code_switch(text, speaker)
            elif self.family == "proper_nouns":
                text = self._apply_proper_nouns(text, speaker)

        text = fix_particles(" ".join(text.split()))

        gap = self.rng.uniform(0.25, 0.75)
        overlap = 0.0
        prev_speaker = self.turns[-1].speaker if self.turns else None
        if (self.family == "overlap_crosstalk" and self.turns
                and prev_speaker != speaker and self.rng.random() < 0.45):
            # Pull this turn back over the tail of the previous one. Only across a SPEAKER
            # CHANGE -- the first version checked nothing but a coin flip, so consecutive
            # turns by the same person could "overlap", which is one voice talking over
            # itself and not a thing that happens on a call.
            #
            # Capped well under a second: past that the two voices stop being separable by
            # ear, and a reference transcript nobody can verify by listening is not ground
            # truth.
            overlap = self.rng.uniform(0.25, 0.70)
            gap = 0.0

        rate, pitch = "+0%", "+0Hz"
        if self.family == "disfluency":
            rate = self.rng.choice(["-8%", "-4%", "+0%", "+6%"])
        if kind == "ivr":
            rate, pitch = "-4%", "+0Hz"

        turn = C.Turn(
            idx=len(self.turns),
            speaker=speaker if kind == "speech" else "ivr",
            text=text,
            gap_before_s=round(gap, 3),
            rate=rate,
            pitch=pitch,
            overlap_s=round(overlap, 3),
            entities=self._entities_in(text) if kind == "speech" else [],
            kind=kind,
        )
        self.turns.append(turn)
        self.est_s += len(text) / CHARS_PER_SEC + gap - overlap

    def add_hold(self, seconds: float) -> None:
        """A hold segment: no speech, filled with hold music by the synthesiser."""
        turn = C.Turn(
            idx=len(self.turns),
            speaker="ivr",
            text="",
            gap_before_s=0.0,
            kind="hold",
            hold_s=round(seconds, 2),
        )
        self.turns.append(turn)
        self.est_s += seconds

    # -- the arc ------------------------------------------------------------------------

    def build(self) -> C.Dialogue:
        if self.family == "hold_ivr":
            self.add("ivr", T.IVR_PROMPTS, kind="ivr")

        self.add("agent", T.GREETING_OUT if self.direction == "OUT" else T.GREETING_IN)
        self.add("customer", T.GREETING_REPLY)
        self.add("customer", T.PROBLEM[self.scenario])
        self.add("agent", T.BACKCHANNEL_AGENT)

        # Identity verification -- always present, because production's QA rubric scores
        # `customer_verification` and `data_privacy` as their own fields.
        # Ask for the number, then get the number. A second identity challenge follows on
        # roughly half the calls, drawn from the pool whose replies actually answer it --
        # these were one pool once, and an agent asking for a date of birth was answered
        # with a mobile number on three exchanges out of five.
        self.add("agent", T.VERIFY_ASK_PHONE)
        self.add("customer", T.VERIFY_PHONE_REPLY)
        self.add("agent", T.VERIFY_CONFIRM)
        self.add("customer", T.VERIFY_YES)
        if self.rng.random() < 0.55:
            # Ask and reply come from the SAME exchange, so the answer answers the
            # question. Drawing them from two flat pools let an agent ask for the last four
            # digits of an ID card and be told a date of birth.
            exchange = self.rng.choice(T.VERIFY_IDENTITY_EXCHANGES)
            self.add("agent", exchange["ask"])
            self.add("customer", exchange["reply"])
        self.add("agent", T.VERIFY_DONE)

        self.add("agent", T.PROBE[self.scenario])
        self.add("customer", T.FILLER_CUSTOMER)

        if self.family == "hold_ivr":
            self.add("agent", T.HOLD_REQUEST)
            self.add_hold(self.rng.uniform(8, 16))
            self.add("ivr", T.IVR_PROMPTS, kind="ivr")
            self.add_hold(self.rng.uniform(6, 12))
            self.add("agent", T.HOLD_RETURN)

        self.add("agent", T.OFFER[self.scenario])

        # Fill to target with the negotiation loop. Reserve room for the closing block so
        # a long filler turn can never push the call past its target by overshooting.
        reserve = 42.0
        guard = 0
        while self.est_s < self.target_s - reserve and guard < 400:
            guard += 1
            roll = self.rng.random()
            if roll < 0.26:
                self.add("customer", T.OBJECTION)
                self.add("agent", T.COUNTER)
            elif roll < 0.48:
                self.add("customer", T.FILLER_CUSTOMER)
                self.add("agent", T.FILLER_AGENT)
            elif roll < 0.60:
                self.add("agent", T.PROBE[self.scenario])
                self.add("customer", T.BACKCHANNEL_CUSTOMER)
            elif roll < 0.70:
                self.add("customer", T.CLARIFY)
                self.add("agent", T.FILLER_AGENT)
            elif roll < 0.78:
                self.add("agent", T.OFFER[self.scenario])
                self.add("customer", T.BACKCHANNEL_CUSTOMER)
            elif roll < 0.85 and self.family == "hold_ivr":
                # hold_ivr ONLY. `long_context` was in this branch in the first build and
                # picked up 80 s of hold music in a 578 s call -- 14% non-speech. That
                # confounds the family: a difference between long_context and
                # clean_baseline could then be caused by the hold rather than by length,
                # which is the one thing the family exists to isolate. Real long calls do
                # contain more hold; measuring one factor at a time matters more here.
                self.add("agent", T.HOLD_REQUEST)
                self.add_hold(self.rng.uniform(5, 12))
                self.add("agent", T.HOLD_RETURN)
            elif roll < 0.92:
                self.add("agent", T.SLA)
                self.add("customer", T.BACKCHANNEL_CUSTOMER)
            else:
                self.add("agent", T.SMALLTALK)
                self.add("customer", T.BACKCHANNEL_CUSTOMER)

        # The outcome was decided in __init__, before a word was written. This renders it.
        # Drawing from the outcome's own pool PLUS the shared pool is what stops the closing
        # sentence from being a lookup table for the label -- see thai_corpus.CUSTOMER_CLOSE.
        self.add("customer", T.CUSTOMER_CLOSE[self.call_result] + T.CUSTOMER_CLOSE_SHARED)
        self.add("agent", T.SLA)
        self.add("agent", T.WRAPUP[self.scenario] + T.WRAPUP_GENERIC)
        self.add("agent", T.ANYTHING_ELSE)
        self.add("customer", T.NOTHING_ELSE)
        self.add("agent", T.CLOSING)
        self.add("customer", T.CLOSING_REPLY)

        meta = C.CallMeta(
            call_id=f"{7100 + self.idx}",
            phone_number=self.phone,
            call_time=f"{self.rng.randint(8, 19):02d}{self.rng.randint(0, 59):02d}{self.rng.randint(0, 59):02d}",
            agent_id=f"A{200 + self.idx:03d}",
            first_name=self.agent_latin[0],
            last_name=self.agent_latin[1],
            provider="D",
            record_date=(BASE_DATE + timedelta(days=self.idx % 14)).strftime("%Y%m%d"),
            duration=int(round(self.est_s)),   # replaced with the measured value later
            call_direction=self.direction,
        )
        errs = meta.validate()
        if errs:
            raise AssertionError(f"{self.idx}: filename contract violated: {errs}")

        return C.Dialogue(
            item_id=f"ASR-{self.idx + 1:03d}",
            family=self.family,
            scenario=self.scenario,
            call_direction=self.direction,
            meta=meta,
            turns=self.turns,
            agent_voice=VOICES[self.agent_gender],
            customer_voice=VOICES[self.cust_gender],
            target_duration_s=self.target_s,
            seed=self.seed,
            notes=T.__name__,
        )


def main() -> None:
    C.DIALOGUE_DIR.mkdir(parents=True, exist_ok=True)
    C.GROUND_TRUTH_DIR.mkdir(parents=True, exist_ok=True)

    index = []
    business: list[B.BusinessLabel] = []
    for i, (family, scenario) in enumerate(PLAN):
        comp = Composer(i, family, scenario, TARGETS[i])
        dlg = comp.build()

        payload = dlg.to_json()
        (C.DIALOGUE_DIR / f"{dlg.item_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (C.GROUND_TRUTH_DIR / f"{dlg.item_id}.txt").write_text(
            dlg.reference_transcript() + "\n", encoding="utf-8"
        )
        (C.GROUND_TRUTH_DIR / f"{dlg.item_id}.entities.json").write_text(
            json.dumps([asdict(e) for e in dlg.all_entities()], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        speech = [t for t in dlg.turns if t.kind == "speech"]
        index.append(
            {
                "item_id": dlg.item_id,
                "family": family,
                "scenario": scenario,
                "direction": dlg.call_direction,
                "target_s": comp.target_s,
                "estimated_s": round(comp.est_s, 1),
                "turns": len(dlg.turns),
                "speech_turns": len(speech),
                "chars": sum(len(t.text) for t in speech),
                "entities": len(dlg.all_entities()),
                "agent_voice": dlg.agent_voice,
                "customer_voice": dlg.customer_voice,
                # The business label, carried alongside the acoustic metadata so a reader of
                # index.json can see the whole design of a call in one place.
                "product": comp.product,
                "call_result": comp.call_result,
                "main": comp.main,
                "secondary": comp.secondary,
            }
        )
        business.append(comp.business_label())
        print(
            f"{dlg.item_id}  {family:19s} {scenario:16s} {dlg.call_direction:3s} "
            f"target={comp.target_s:4d}s est={comp.est_s:6.1f}s "
            f"turns={len(dlg.turns):3d} chars={index[-1]['chars']:5d} "
            f"ents={index[-1]['entities']:3d}"
        )

    (C.DIALOGUE_DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # business.csv -- the file that turns this from an ASR fixture into an end-to-end one.
    # Columns and order match tests/fixtures/testsets/retention_v3.gt.csv exactly, so
    # evalharness.metrics scores this set through the same code path as the text packs
    # rather than a parallel implementation that could drift from it.
    gt_path = C.GROUND_TRUTH_DIR / "business.csv"
    with gt_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(B.BusinessLabel.header())
        for label in business:
            writer.writerow(label.row())

    total = sum(r["estimated_s"] for r in index)
    print(f"\n{len(index)} dialogues, estimated total {total / 60:.1f} min")

    # Print the realised mix. The targets in business_labels.py are a distribution, not a
    # quota, so what actually landed is a fact about this build and belongs in the log.
    print(f"wrote {gt_path.name}: {len(business)} rows")
    for field in ("product", "call_result", "main"):
        counts: dict[str, int] = {}
        for label in business:
            counts[getattr(label, field)] = counts.get(getattr(label, field), 0) + 1
        rendered = "  ".join(
            f"{k or '(empty)'}={v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
        )
        print(f"  {field:12} {rendered}")


if __name__ == "__main__":
    main()
