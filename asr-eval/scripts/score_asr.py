"""Score an ASR arm's transcripts against the authored ground truth.

Four numbers, because no single one of them is the answer:

  CER    character error rate. The primary metric for Thai. Thai is written without word
         spaces, so a character-level distance is the only figure that does not depend on
         a tokeniser's opinion.

  WER    word error rate over pythainlp `newmm` tokens. Reported because everyone asks for
         it, and flagged for what it is: TOKENISER-DEPENDENT. Two arms whose Thai is
         equally good can post different WERs if one of them spaces words differently.
         Both sides go through the same tokeniser here, which removes most but not all of
         that. Read CER first.

  ENTITY  did the phone number / amount / date / package survive? WER treats a wrong digit
         in a mobile number exactly like a wrong final particle. Production does not: the
         QA pipeline writes that number into a field. This is the metric that maps to
         money, and it is reported per type.

  INSERT  a hallucination PROXY: total word insertions divided by the minutes of audio
         containing no speech. An arm that invents dialogue over hold music shows up here
         while looking acceptable on a pooled WER, which is what the `hold_ivr` family
         exists to expose.

         Read it as a proxy and nothing more. A plain transcript carries no timestamps, so
         this CANNOT establish that an insertion happened during the hold music -- only
         that the arm emitted more words than the reference on a file that contains that
         much non-speech. To make it a direct measurement the arm would have to return
         word- or segment-level timings, which most transcription endpoints will do on
         request; wiring that up is the obvious next improvement to this scorer.

Every metric is computed twice: RAW, and after asr_common.normalise_thai. The gap between
the two is how much of an arm's error is lossless orthographic difference rather than a
real mishearing. The normalisation is deliberately narrow -- see the note in asr_common
and ASR-EXPECTATION.md:52-59 on why forgiving inference would hide fabrication.

INPUT FORMAT
    One UTF-8 .txt per call in --hyp-dir, named <ITEM_ID>.txt (e.g. ASR-003.txt), holding
    the arm's transcript. Line breaks are not significant; the scorer flattens both sides.
    Alternatively a single .jsonl with {"item_id": ..., "text": ...} per line.

Run:
    python asr-eval/scripts/score_asr.py --hyp-dir hypotheses/qwen3-asr
    python asr-eval/scripts/score_asr.py --hyp-dir hypotheses/qwen3-asr --jobs 1
    python asr-eval/scripts/score_asr.py --self-test

Scoring runs across a process pool by default; `--jobs 1` forces the serial path, which is
what to run when a pooled figure needs corroborating. The two must agree byte for byte --
see "Running the pool" below and tests/test_provenance.py, which asserts exactly that.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import asr_common as C
import thai_num as N

try:
    from pythainlp.tokenize import word_tokenize
except Exception:                                            # noqa: BLE001
    word_tokenize = None


# --------------------------------------------------------------------------------------
# Edit distance
# --------------------------------------------------------------------------------------


def levenshtein(a: list, b: list) -> tuple[int, int, int, int]:
    """Return (distance, substitutions, deletions, insertions) via a full DP traceback.

    Written out rather than imported so the S/D/I split is available: a single distance
    cannot distinguish an arm that drops half the call (deletions) from one that invents
    a second call on top of it (insertions), and those are opposite failures.
    """
    n, m = len(a), len(b)
    if n == 0:
        return m, 0, 0, m
    if m == 0:
        return n, 0, n, 0

    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        ai = a[i - 1]
        di, dp = d[i], d[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ai == b[j - 1] else 1
            di[j] = min(dp[j] + 1, di[j - 1] + 1, dp[j - 1] + cost)

    sub = dele = ins = 0
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (0 if a[i - 1] == b[j - 1] else 1):
            if a[i - 1] != b[j - 1]:
                sub += 1
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            dele += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return d[n][m], sub, dele, ins


try:                                                    # optional accelerator
    from rapidfuzz.distance import Levenshtein as _RF
except ImportError:                                     # pragma: no cover
    _RF = None


def fast_distance(a: str, b: str) -> int:
    """Edit distance only, for the diagnostic. Falls back to the DP above when unavailable.

    Verified equal to `levenshtein()[0]` on 400 random pairs and on the real transcripts.
    Only the DISTANCE is used -- see the note in score_item on why the split is not.
    """
    if _RF is not None:
        return _RF.distance(a, b)
    return levenshtein(list(a), list(b))[0]


def fast_distance_seq(a: list, b: list) -> int:
    if _RF is not None:
        return _RF.distance(a, b)
    return levenshtein(a, b)[0]


def tokenise(text: str) -> list[str]:
    """Thai word tokens. Falls back to characters if pythainlp is unavailable.

    The fallback is loud rather than silent: a WER computed over characters is a CER
    wearing the wrong label, and reporting it as WER would be a quiet lie.
    """
    if word_tokenize is None:
        raise RuntimeError(
            "pythainlp is not installed, so word-level WER cannot be computed. "
            "Install it (see requirements-asr.txt) or read CER, which needs no tokeniser."
        )
    return [t for t in word_tokenize(text, engine="newmm") if t.strip()]


def chars(text: str) -> list[str]:
    return [c for c in text if not c.isspace()]


# --------------------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------------------

_DIGIT_WORD = {w: str(i) for i, w in enumerate(N.DIGITS)}
_DIGIT_RE = re.compile("|".join(sorted(N.DIGITS, key=len, reverse=True)))


def spoken_digit_runs(text: str) -> list[str]:
    """Every run of consecutive Thai digit-words, collapsed to its digit string.

    'ศูนย์ แปด หนึ่ง ศูนย์ ...' -> '0810...'. This is how a phone number read aloud comes
    back, and it is the only way to compare it against the canonical value.
    """
    runs: list[str] = []
    cur: list[str] = []
    pos = 0
    t = C.normalise_thai(text)
    while pos < len(t):
        m = _DIGIT_RE.match(t, pos)
        if m:
            cur.append(_DIGIT_WORD[m.group(0)])
            pos = m.end()
            while pos < len(t) and t[pos] == " ":
                pos += 1
        else:
            if len(cur) >= 3:
                runs.append("".join(cur))
            cur = []
            pos += 1
    if len(cur) >= 3:
        runs.append("".join(cur))
    return runs


# Unit words a bare numeral must sit beside before it counts as that entity's value.
# Without this, "12" recovered from the date "12 สิงหาคม" would be credited as a 12-month
# contract term.
UNIT_WORDS: dict[str, tuple[str, ...]] = {
    "amount": ("บาท",),
    "months": ("เดือน",),
    "speed": ("เมกะบิต", "mbps", "เมก"),
}


def numeric_runs(text: str) -> set[str]:
    """Every MAXIMAL ASCII digit run, with thousands separators removed.

    Maximal is the whole point. The first version compared against
    ``C.digits_only(hyp)`` -- every digit in the transcript concatenated into one string
    with all separators stripped -- and asked whether the entity's value appeared in it as
    a SUBSTRING. That credits an entity the transcript never mentions:

        amount 599  vs  "ส่วนลด 45 บาท และใช้ได้ 99 วัน"   -> "45"+"99" = "4599" contains "599"
        months 12   vs  "ค่าบริการรอบนี้ 1250 บาทครับ"      -> "1250" contains "12"

    Scored against a junk hypothesis mentioning none of the call, the old code returned
    57/498 entities "recovered" -- 11.4% accuracy for a transcript of nothing, and every
    one of those counted as a `value` hit rather than a surface hit, so the split that was
    supposed to expose the problem hid it instead.
    """
    return set(re.findall(r"\d+", strip_thousands(text)))


def strip_thousands(text: str) -> str:
    """Normalised text with thousands separators removed: '1,290' -> '1290'.

    Used by BOTH numeric_runs and the unit-adjacency check. Applying it to only one of them
    is a real bug and was one: runs saw '1290' while the adjacency search still saw
    '1,290 บาท', so a correctly-transcribed amount written with a comma scored as a miss.
    """
    return re.sub(r"(?<=\d)[,](?=\d)", "", C.normalise_thai(text))


def _numeral_beside_unit(text_norm: str, digits: str, units: tuple[str, ...]) -> bool:
    """True if `digits` appears as a whole number within ~15 chars of one of `units`."""
    for u in units:
        pattern = rf"(?<!\d){re.escape(digits)}(?!\d)[^\d]{{0,15}}?{re.escape(u)}"
        if re.search(pattern, text_norm, flags=re.IGNORECASE):
            return True
    return False


def score_entities(entities: list[dict], hyp: str) -> dict:
    """Per-type hit counts.

    An entity counts as recovered if EITHER the spoken surface form survives, OR the
    canonical value is recoverable from the hypothesis. Both are recorded, because an arm
    that writes '0810000301' where the audio said the digits one at a time is correct in
    substance and would be scored wrong by surface match alone.

    The value path is deliberately strict -- whole digit runs, and for the unit-bearing
    types the numeral must sit beside its unit word. A generous value path does not make an
    arm look slightly better, it makes the metric report recoveries that never happened.
    """
    nh = C.normalise_thai(hyp)
    nh_nospace = nh.replace(" ", "")        # for the surface test only -- see below
    nh_nums = strip_thousands(hyp)          # same text, thousands separators removed
    runs = numeric_runs(hyp) | set(spoken_digit_runs(hyp))

    per_type: dict[str, dict] = {}
    details = []
    for e in entities:
        etype, value, spoken = e["type"], e["value"], e["spoken"]
        # Whitespace-insensitive on BOTH sides. Thai has no inter-word spaces, so an arm that
        # writes the same words with different phrase spacing has not lost the entity -- and
        # before this, 90% of one arm's "missed" entities were present and spaced differently.
        # Surface path only: `_numeral_beside_unit` below still runs on spaced text, so
        # RET-115's load-bearing space between an amount and its unit is untouched.
        surface = C.normalise_thai(spoken).replace(" ", "") in nh_nospace
        value_hit = False
        if etype in ("phone", "id"):
            # A phone or case id is long and distinctive, so a whole-run match is enough.
            value_hit = value in runs
        elif etype in UNIT_WORDS:
            digits = re.sub(r"\D", "", value)
            value_hit = bool(digits) and digits in runs and _numeral_beside_unit(
                nh_nums, digits, UNIT_WORDS[etype]
            )
        elif etype == "date":
            # `date` was missing from this chain entirely, so no date could EVER score a
            # value hit -- contradicting this function's own docstring. A date counts when
            # the day appears as a whole number and the Thai month name is present.
            try:
                _y, mm, dd = value.split("-")
                month_name = N.THAI_MONTHS[int(mm) - 1]
                value_hit = str(int(dd)) in runs and month_name in nh
            except (ValueError, IndexError):
                value_hit = False
        elif etype == "package":
            value_hit = C.normalise_thai(value) in nh
        hit = surface or value_hit
        slot = per_type.setdefault(etype, {"total": 0, "hit": 0, "surface": 0, "value": 0})
        slot["total"] += 1
        slot["hit"] += int(hit)
        slot["surface"] += int(surface)
        slot["value"] += int(value_hit)
        if not hit:
            details.append({"type": etype, "value": value, "spoken": spoken[:60]})

    total = sum(v["total"] for v in per_type.values())
    hit = sum(v["hit"] for v in per_type.values())
    return {
        "per_type": per_type,
        "total": total,
        "hit": hit,
        "accuracy": (hit / total) if total else None,
        "missed": details,
    }


# --------------------------------------------------------------------------------------
# Non-speech insertion probe
# --------------------------------------------------------------------------------------


def nonspeech_seconds(timeline: dict, duration_s: float) -> float:
    """Total audio time containing no speech segment at all."""
    segs = sorted(
        (s for s in timeline.get("segments", []) if s["kind"] == "speech"),
        key=lambda s: s["start_s"],
    )
    covered, cursor = 0.0, 0.0
    for s in segs:
        start, end = s["start_s"], s["start_s"] + s["dur_s"]
        if start > cursor:
            cursor = start
        covered += max(0.0, end - cursor)
        cursor = max(cursor, end)
    return max(0.0, duration_s - covered)


# --------------------------------------------------------------------------------------
# Scoring one item
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# Runaway detection
# --------------------------------------------------------------------------------------
# A repetition loop is a decoder failure, not a transcription error, and pooling it as if
# it were one produces a number that describes no call in the set. On 2026-08-17 exactly
# this happened: ASR-012 returned 58,546 characters against a 3,405-character reference for
# a CER of 16.28, that item was pooled with nineteen items scoring ~0.1, and the corpus CER
# published for the arm was 0.673. Re-transcribed without the loop the same arm scores
# 0.1147. Every voice-track figure derived from 0.673 was wrong by a factor of six.
#
# The insertion diagnostic ALREADY caught it -- 20,972 insertions per non-speech minute
# against a set mean of 1,073, a 20x outlier on a metric built for exactly this -- and
# nothing acted on it. That is the gap this closes: detection existed, refusal did not.
#
# The check runs BEFORE the edit distance for a second, practical reason. The DP is
# O(len(ref) x len(hyp)); on a 39,689-character runaway that is ~320M cells in pure Python.
# It was measured at over 30 minutes before being killed. Refusing costs microseconds.
#
# EXCLUDED IS NOT FORGIVEN. A runaway is the worst outcome an ASR arm can produce, so
# dropping it from the CER pool must never let an arm look better. Excluded items are
# counted, named, and reported as a failure rate beside every pooled figure.

RUNAWAY_LENGTH_RATIO = 1.5      # hypothesis longer than this multiple of its reference
RUNAWAY_REPEAT_COVERAGE = 0.20  # a single repeated unit covering this much of the output
RUNAWAY_UNIT_RANGE = (20, 400)  # unit lengths scanned for a repeat


def repeated_unit_coverage(text: str) -> tuple[float, int, int]:
    """Largest fraction of `text` covered by one repeated fixed-length substring.

    Returns (coverage, unit_length, repeat_count). Probes three offsets rather than every
    position: a loop long enough to matter occupies most of the output, so it is present at
    the quarter, half and three-quarter marks. Scanning every offset would be quadratic for
    no additional catch.
    """
    n = len(text)
    lo, hi = RUNAWAY_UNIT_RANGE
    if n < lo * 4:
        return (0.0, 0, 0)
    best = (0.0, 0, 0)
    for probe in (n // 4, n // 2, 3 * n // 4):
        for unit in range(lo, min(hi, n // 4)):
            segment = text[probe:probe + unit]
            if len(segment) < unit:
                continue
            count = text.count(segment)
            if count >= 3:
                coverage = count * unit / n
                if coverage > best[0]:
                    best = (coverage, unit, count)
    return best


def detect_runaway(ref: str, hyp: str) -> dict | None:
    """Why this hypothesis cannot be scored as a transcript, or None if it can."""
    ref_n, hyp_n = len(chars(ref)), len(chars(hyp))
    if ref_n and hyp_n > RUNAWAY_LENGTH_RATIO * ref_n:
        return {
            "kind": "length",
            "detail": (f"hypothesis is {hyp_n} characters against a {ref_n}-character "
                       f"reference ({hyp_n / ref_n:.2f}x, limit "
                       f"{RUNAWAY_LENGTH_RATIO})"),
            "ratio": round(hyp_n / ref_n, 3),
        }
    # The repetition rule applies only to a hypothesis long enough to have run away. A
    # SHORT degenerate output -- the model emitting a hundred characters of one syllable
    # against a thousand-character reference -- is a different failure, and CER already
    # describes it correctly as deletion-dominated. Calling it a runaway would remove a
    # real, badly-performing item from the corpus rate and flatter the arm.
    if hyp_n < 0.5 * ref_n:
        return None
    coverage, unit, count = repeated_unit_coverage(hyp)
    if coverage > RUNAWAY_REPEAT_COVERAGE:
        return {
            "kind": "repetition",
            "detail": (f"a {unit}-character unit repeats {count} times, covering "
                       f"{coverage * 100:.1f}% of the output (limit "
                       f"{RUNAWAY_REPEAT_COVERAGE * 100:.0f}%)"),
            "coverage": round(coverage, 4),
            "unit_chars": unit,
            "repeats": count,
        }
    return None


def score_item(item: str, ref: str, hyp: str, entities: list[dict],
               timeline: dict, duration_s: float, family: str) -> dict:
    ref_flat = " ".join(ref.split())
    hyp_flat = " ".join(hyp.split())

    runaway = detect_runaway(ref_flat, hyp_flat)
    if runaway is not None:
        # Return early, before the edit distance. Every metric field is absent rather than
        # zero or None-with-a-number: a runaway has no meaningful CER, and emitting one
        # invites it back into an average.
        return {
            "item_id": item, "family": family,
            "ref_chars": len(chars(ref_flat)), "hyp_chars": len(chars(hyp_flat)),
            "runaway": runaway,
        }

    out: dict = {"item_id": item, "family": family,
                 "ref_chars": len(chars(ref_flat)), "hyp_chars": len(chars(hyp_flat))}

    _norm_ref, _norm_hyp = C.normalise_thai(ref_flat), C.normalise_thai(hyp_flat)
    for label, r, h in (("raw", ref_flat, hyp_flat),
                        ("norm", _norm_ref, _norm_hyp)):
        rc, hc = chars(r), chars(h)
        dist, sub, dele, ins = levenshtein(rc, hc)
        out[f"cer_{label}"] = dist / len(rc) if rc else None
        out[f"cer_{label}_sdi"] = [sub, dele, ins]
        # Recorded per label: normalisation can change the character count (it strips
        # zero-width characters and collapses เเ to แ), so a single ref_chars would be the
        # wrong denominator for one of the two.
        out[f"ref_chars_{label}"] = len(rc)

        try:
            rt, ht = tokenise(r), tokenise(h)
            wdist, wsub, wdel, wins = levenshtein(rt, ht)
            out[f"wer_{label}"] = wdist / len(rt) if rt else None
            out[f"wer_{label}_sdi"] = [wsub, wdel, wins]
            out[f"ref_words_{label}"] = len(rt)
        except RuntimeError as exc:
            out[f"wer_{label}"] = None
            out["wer_error"] = str(exc)

    # The whitespace-blind DIAGNOSTIC. Not the contract metric, and deliberately reports no
    # S/D/I split: it is computed by a different implementation whose split can differ from
    # the DP above on an equally-optimal alignment. Rate and denominator only, so nothing
    # published depends on how this one apportions its errors.
    rns, hns = _norm_ref.replace(" ", ""), _norm_hyp.replace(" ", "")
    out["cer_nospace_errors"] = fast_distance(rns, hns)
    out["ref_chars_nospace"] = len(chars(rns))
    out["cer_nospace"] = (out["cer_nospace_errors"] / out["ref_chars_nospace"]
                          if out["ref_chars_nospace"] else None)
    try:
        rt, ht = tokenise(rns), tokenise(hns)
        out["wer_nospace_errors"] = fast_distance_seq(rt, ht)
        out["ref_words_nospace"] = len(rt)
        out["wer_nospace"] = (out["wer_nospace_errors"] / len(rt)) if rt else None
    except RuntimeError:
        out["wer_nospace"] = None

    out["entity"] = score_entities(entities, hyp_flat)

    ns = nonspeech_seconds(timeline, duration_s)
    out["nonspeech_s"] = round(ns, 2)
    ins_words = out.get("wer_norm_sdi", [0, 0, 0])[2]
    out["insertions_per_nonspeech_min"] = round(ins_words / (ns / 60), 2) if ns > 1 else None
    return out


# --------------------------------------------------------------------------------------
# Running the pool
# --------------------------------------------------------------------------------------
# score_item is O(len(ref) x len(hyp)) pure Python, and it runs that DP four times per item:
# characters and word tokens, raw and normalised. Twenty items took about ten minutes. The
# 138-call set would take about an hour and a half per arm.
#
# That cost is not merely slow, it is a correctness risk, and it is the specific one this
# project keeps hitting: a scorer nobody wants to wait for is a scorer people run on five
# items and compare against last week's twenty-item figure. Two numbers with different
# denominators, both called "the CER".
#
# So: the same function, on more cores, and NOTHING else changes.
#
#   * score_item is untouched. No shared cache, no early exit, no reordering.
#   * The runaway check still runs BEFORE the distance, inside score_item, where it always
#     did. Parallelism does not make a 320M-cell DP affordable; it makes it 320M cells on a
#     different core.
#   * No faster edit distance. rapidfuzz agrees with levenshtein() on the DISTANCE and
#     disagrees on the S/D/I split on 96 of 400 random pairs -- equally optimal alignments,
#     apportioned differently -- and that split is what separates an arm that dropped a
#     passage from one that invented it. fast_distance() uses it for the whitespace-blind
#     diagnostic only, which is why that diagnostic publishes no split.
#
# ORDER IS SUBMISSION ORDER, ALWAYS. `Executor.map` yields results in the order the tasks
# went in, whatever order the workers finish in, so `rows` never depends on scheduling and
# two runs of the same arm serialise to identical bytes. Anything built on
# `as_completed()` would reorder the report between runs for free, and a report whose row
# order moves cannot be diffed against its predecessor.


def _check_jobs(value: int) -> int:
    """The one place --jobs is validated, so the CLI and the API refuse identically."""
    if value < 1:
        raise ValueError(
            f"--jobs must be at least 1, got {value}. 1 is the serial path: every item "
            f"scored in this process, which is what to run when a pooled figure needs "
            f"corroborating."
        )
    return value


def _jobs_arg(text: str) -> int:
    try:
        return _check_jobs(int(text))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def resolve_jobs(requested: int | None, n_items: int) -> int:
    """Worker processes to actually use. 1 means run in this process."""
    if requested is not None:
        _check_jobs(requested)
    if n_items < 1:
        return 1
    if requested is None:
        # One worker per core, never more than there is work. A spawned worker on Windows
        # costs an interpreter start plus a pythainlp dictionary load -- about a second --
        # and a pool of fourteen against three items pays that fourteen times to do three
        # items' work.
        return max(1, min(os.cpu_count() or 1, n_items))
    return min(requested, n_items)


def _score_one(task: tuple) -> dict:
    """Unpack one task tuple.

    ProcessPoolExecutor has no starmap and a lambda cannot be pickled to a worker, so the
    adapter has to be a module-level function. It exists for pickling and for nothing else:
    do not let it grow logic, or the serial and parallel paths stop being the same code.
    """
    return score_item(*task)


def score_items(tasks: list[tuple], jobs: int) -> list[dict]:
    """Score every task, in submission order, across `jobs` processes."""
    if jobs <= 1:
        return [_score_one(t) for t in tasks]
    with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as pool:
        # chunksize stays at its default of 1. Per-item cost spans four orders of magnitude
        # here -- a runaway refuses in microseconds, a ten-minute call runs the full DP --
        # so handing each worker a fixed batch would hand one of them every expensive item
        # and leave the rest idle.
        return list(pool.map(_score_one, tasks))


# --------------------------------------------------------------------------------------
# Self-test: prove the scorer moves in the right direction before trusting it on an arm
# --------------------------------------------------------------------------------------


# A probe string for the three LOSSLESS artifact classes.
#
# It exists because the obvious approach -- corrupt a real reference -- cannot exercise
# them. The composer spells every number out as Thai words (thai_num.read_digits and
# friends), so a reference transcript contains almost no numerals in either script, and an
# "ASCII digits -> Thai digits" corruption applied to one is a no-op that scores a
# reassuring 0.0000 while testing nothing. The first version of this self-test did exactly
# that and passed.
#
# So: the lossless classes are checked against this probe, which is guaranteed to contain
# the characters at issue, and the scale/direction checks below are run against a real
# reference where corpus-sized behaviour is what matters.
PROBE_TEXT = (
    "โทร ๑๒๓๑ ได้ตลอดยี่สิบสี่ชั่วโมง แล้วกดหมายเลข ๐๘๑๐๐๐๐๓๐๑ "
    "แพ็กเกจนี้ราคา ๕๙๙ บาท และแถมเน็ตอีก ๑๐ กิกะไบต์"
)


def corrupt(text: str, kind: str, rng) -> str:
    """Apply one of the artifact classes ASR-EXPECTATION.md enumerates, to a reference."""
    if kind == "identity":
        return text
    if kind == "arabic_digits":                     # class 3, RET-115: lossless
        # The arm wrote ASCII where the reference has Thai numerals. RET-115's mapping is
        # total and unambiguous in both directions, so normalisation must erase this.
        return text.translate(str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789"))
    if kind == "sara_e":                            # class 9, RET-121: lossless control
        return text.replace("แ", "เเ")
    if kind == "zero_width":                        # class 10, RET-122: lossless control
        out = []
        for i, ch in enumerate(text):
            out.append(ch)
            if i % 37 == 0:
                out.append("​")
        return "".join(out)
    if kind == "tone_marks":                        # class 1, RET-113: LOSSY
        return re.sub(r"[่-๋]", "", text)
    if kind == "drop_words":
        toks = text.split(" ")
        return " ".join(t for i, t in enumerate(toks) if i % 9 != 0)
    if kind == "truncate_half":
        return text[: len(text) // 2]
    if kind == "hallucinate":
        # Deliberately kept BELOW the runaway thresholds. This case exists to prove the
        # scorer can tell an arm that invents from one that gives up (insertions vs
        # deletions), which requires it to remain scoreable. The stronger version is
        # `runaway_loop`, whose whole point is that it must NOT be scoreable.
        return text + " " + "ขอบคุณครับ ยินดีให้บริการครับ " * 8
    if kind == "runaway_loop":
        # The shape of the real ASR-012 failure: a short unit repeated until it dominates
        # the output. detect_runaway must refuse this before the edit distance runs.
        return text + " " + "ขอบคุณครับ ยินดีให้บริการครับ " * 60
    if kind == "runaway_inplace":
        # A loop that REPLACES content instead of appending it, so the output stays inside
        # the 1.5x length limit and only the repetition rule can catch it. Without this
        # case the coverage rule is dead code that no test would notice losing.
        unit = "ขอบคุณครับ ยินดีให้บริการครับ "
        head = text[: len(text) // 4]
        return head + unit * (3 * len(text) // (4 * len(unit)))
    raise ValueError(kind)


def self_test() -> int:
    """Score fabricated hypotheses whose ORDERING is known in advance.

    The expectations are written here as assertions rather than printed as output, in the
    discipline CLAUDE.md sets: a metric that has never been shown to move is not evidence.
    """
    import random

    rng = random.Random(7)
    items = sorted(C.GROUND_TRUTH_DIR.glob("ASR-*.txt"))
    if not items:
        print("no ground truth; run compose_dialogues.py first")
        return 1
    ref = items[0].read_text(encoding="utf-8")

    failures: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)

    def run(base: str, kind: str) -> dict:
        s = score_item("SELFTEST", base, corrupt(base, kind, rng), [],
                       {"segments": []}, 300.0, "selftest")
        print(f"  {kind:15s} CER raw={s['cer_raw']:.4f}  norm={s['cer_norm']:.4f}  "
              f"S/D/I={s['cer_norm_sdi']}")
        return s

    # --- half 1: the lossless classes, on the probe -----------------------------------
    print("lossless classes (probe text, guaranteed to contain the characters at issue):")
    # Guard the guard: if the probe ever stops containing Thai numerals, say so loudly
    # rather than passing a no-op test.
    expect(any(c in PROBE_TEXT for c in "๐๑๒๓๔๕๖๗๘๙"),
           "PROBE_TEXT no longer contains Thai numerals, so arabic_digits tests nothing")
    expect("แ" in PROBE_TEXT, "PROBE_TEXT no longer contains แ, so sara_e tests nothing")

    lossless = {}
    for kind in ("identity", "arabic_digits", "sara_e", "zero_width"):
        lossless[kind] = run(PROBE_TEXT, kind)

    expect(lossless["identity"]["cer_raw"] == 0.0, "identity must score exactly 0 raw CER")
    expect(lossless["identity"]["cer_norm"] == 0.0, "identity must score exactly 0 norm CER")
    for k in ("arabic_digits", "sara_e", "zero_width"):
        expect(lossless[k]["cer_raw"] > 0.0,
               f"{k}: raw CER must SEE the difference (got {lossless[k]['cer_raw']:.5f}) -- "
               f"a 0 here means the corruption was a no-op and the class is untested")
        expect(lossless[k]["cer_norm"] == 0.0,
               f"{k}: normalisation must fully forgive it (got {lossless[k]['cer_norm']:.5f})")

    # --- half 2: lossy classes and direction, on a real reference ---------------------
    print("\nlossy classes and error direction (real reference, corpus-sized):")
    lossy = {}
    for kind in ("identity", "tone_marks", "drop_words", "truncate_half", "hallucinate"):
        lossy[kind] = run(ref, kind)

    expect(lossy["identity"]["cer_norm"] == 0.0, "a real reference must score 0 against itself")

    # Tone-mark loss is the class ASR-EXPECTATION.md refuses to forgive, because repairing
    # it is guessing. It must survive normalisation.
    expect(lossy["tone_marks"]["cer_norm"] > 0.0,
           "tone_marks must NOT be forgiven: repairing it is inference, not reading")

    # Deletions must dominate a truncation and insertions a hallucination. A metric that
    # cannot tell those apart cannot tell an arm that gives up from one that invents.
    _, dele, ins = lossy["truncate_half"]["cer_norm_sdi"]
    expect(dele > ins * 5, f"truncation must be deletion-dominated (D={dele}, I={ins})")
    _, dele_h, ins_h = lossy["hallucinate"]["cer_norm_sdi"]
    expect(ins_h > dele_h * 5, f"hallucination must be insertion-dominated (D={dele_h}, I={ins_h})")

    expect(lossy["truncate_half"]["cer_norm"] > lossy["drop_words"]["cer_norm"],
           "losing half the call must score worse than dropping every ninth token")

    # --- the runaway gate -------------------------------------------------------------
    # Added after ASR-012 published a CER of 16.28 into a corpus figure. Both directions
    # are checked, because a detector that fires on everything is as useless as one that
    # fires on nothing -- and every scoreable case above has already proved the negative.
    print("\nrunaway detection:")
    loop = score_item("SELFTEST", ref, corrupt(ref, "runaway_loop", rng), [],
                      {"segments": []}, 300.0, "selftest")
    expect("runaway" in loop, "a repeated unit dominating the output must be refused")
    if "runaway" in loop:
        print(f"  runaway_loop    caught: {loop['runaway']['kind']} -- "
              f"{loop['runaway']['detail']}")
        expect("cer_norm" not in loop,
               "a refused item must carry NO error rate: emitting one invites it back "
               "into an average")
    inplace = score_item("SELFTEST", ref, corrupt(ref, "runaway_inplace", rng), [],
                         {"segments": []}, 300.0, "selftest")
    expect("runaway" in inplace, "an in-place loop must be refused even inside the "
                                 "length limit")
    if "runaway" in inplace:
        print(f"  runaway_inplace caught: {inplace['runaway']['kind']} -- "
              f"{inplace['runaway']['detail']}")
        expect(inplace["runaway"]["kind"] == "repetition",
               "an in-place loop is within the length limit, so the REPETITION rule must "
               f"be what catches it, not {inplace['runaway']['kind']}")

    for scoreable in ("identity", "tone_marks", "drop_words", "truncate_half",
                      "hallucinate"):
        expect("runaway" not in lossy[scoreable],
               f"{scoreable} is a legitimate transcript and must not be refused")
    print(f"  {len(lossy)} legitimate corruptions all scored, none refused")

    # --- entity scoring must actually fire --------------------------------------------
    print("\nentity recovery:")
    ent_file = C.GROUND_TRUTH_DIR / f"{items[0].stem}.entities.json"
    ents = json.loads(ent_file.read_text(encoding="utf-8")) if ent_file.exists() else []
    if ents:
        perfect = score_entities(ents, ref)
        empty = score_entities(ents, "ไม่มีอะไร")
        print(f"  perfect hypothesis  {perfect['hit']}/{perfect['total']}")
        print(f"  empty hypothesis    {empty['hit']}/{empty['total']}")
        expect(perfect["accuracy"] == 1.0,
               f"the reference must recover 100% of its own entities, got {perfect['accuracy']}")
        expect(empty["hit"] == 0, f"an empty hypothesis must recover none, got {empty['hit']}")
        # A phone read back as digits rather than Thai words is correct in substance.
        phones = [e for e in ents if e["type"] == "phone"]
        if phones:
            digit_hyp = phones[0]["value"]
            got = score_entities([phones[0]], digit_hyp)
            expect(got["hit"] == 1,
                   "a phone written as digits must count as recovered, not as a miss")
            print(f"  phone as digits     {got['hit']}/1")
    else:
        expect(False, "no entities on the first item, so entity scoring is untested")

    print()
    if failures:
        for f in failures:
            print(f"SELF-TEST FAIL: {f}")
        return 1
    print("SELF-TEST PASS: 8 corruption classes and entity recovery, all as predicted")
    return 0


# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------------------
# A report stamps the code that produced it so that a published figure traces back to the
# bytes that computed it. Until 2026-08-18 that stamp was one field -- sha256 of this file's
# bytes ON DISK -- and it could not do the job it was there for.
#
# Both committed reports under asr-eval/reports/ stamp
# scoring_code_sha256 = bed27990c1258617..., and that value matches the sha256 of no
# committed version of this file: HEAD (d568505) hashes to 64667716d387383f... Verification
# of the 2026-08-17 round concluded from this that the reports had been generated from lost
# intermediate code, and wrote that conclusion into two documents.
#
# They had not. bed27990 is HEAD's own blob with every LF expanded to CRLF -- exactly the
# bytes a Windows checkout puts on disk under core.autocrlf, hashed by a stamp that reads
# the working tree. The content was HEAD's the whole time. The stamp was not wrong about
# the bytes it hashed; it was hashing bytes that are a property of the checkout rather than
# of the code, and an audit trail that walks its reader into the wrong conclusion costs the
# same as one that is missing.
#
# Hence four fields, each answering something the others cannot:
#
#   sha256      the bytes as executed. UNCHANGED in meaning -- the two reports above carry
#               this name, and redefining it would silently invalidate every comparison
#               against them.
#   sha256_lf   the same content with line endings normalised to LF. This is the value that
#               survives a checkout on another platform, and the one that can be lined up
#               against what git actually stores.
#   commit      what HEAD was. A hash identifies bytes; only this says whether anybody else
#               can obtain them.
#   dirty       whether the scoring path had uncommitted changes. A commit id on its own is
#               a claim about a tree that may not be the tree that ran.
#
# `dirty` covers asr_common.py and thai_num.py as well as this file, because normalise_thai
# lives in the first and moves every `_norm` figure in the report, and the digit words
# spoken_digit_runs matches on live in the second. A flag that only ever looked at
# score_asr.py would report a clean tree while the normalisation underneath it was
# uncommitted -- the same failure being fixed here, one import away.
#
# When git cannot answer, the fields say so in words. Omitting them would let a report
# generated where no git exists look exactly like one generated from a clean tree.

SCORING_PATH = ("score_asr.py", "asr_common.py", "thai_num.py")

GIT_TIMEOUT_S = 10


def _git(args: list[str], *, cwd: Path, git: str = "git") -> str:
    """stdout of one git command, or OSError carrying why it could not be run.

    Every failure mode folds into OSError deliberately: a missing binary, a directory that
    is not a repository, a repository with no commits yet and a hung index all mean one
    thing to the caller -- git did not answer -- and which one it was belongs in the
    recorded message, not in the control flow.
    """
    try:
        proc = subprocess.run([git, *args], cwd=str(cwd), capture_output=True,
                              text=True, timeout=GIT_TIMEOUT_S)
    except FileNotFoundError as exc:
        raise OSError(f"git executable not found ({exc})") from exc
    except subprocess.TimeoutExpired as exc:
        raise OSError(f"git {' '.join(args)} did not answer within {GIT_TIMEOUT_S}s") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise OSError(f"git {' '.join(args)} exited {proc.returncode}: "
                      f"{detail[0][:160] if detail else 'no message'}")
    return proc.stdout


def file_hashes(raw: bytes) -> dict[str, str]:
    """Both hashes of one file's bytes: as they sit on disk, and line-ending normalised.

    Split out so the invariant can be tested on its own -- the same content written CRLF
    and written LF must DIFFER in `sha256` and AGREE in `sha256_lf`. That divergence is the
    whole 2026-08-18 incident, and a property nobody has watched hold is not a property.
    """
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "sha256_lf": hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(),
    }


def code_provenance(paths: tuple[str, ...] | None = None, *,
                    repo: Path | None = None, git: str = "git") -> dict:
    """Identify the code producing this report: its bytes, its commit, its cleanliness.

    `repo` is where git gets asked, defaulting to this file's directory -- the scoring code
    and the tree it is committed in are the same question. It is a parameter so the tests
    can stand up a real repository and drive clean, dirty and no-commits-yet directly,
    instead of asserting against whatever state this working tree happens to be in.
    """
    here = repo or Path(__file__).resolve().parent
    names = tuple(paths) if paths is not None else SCORING_PATH
    out = dict(file_hashes(Path(__file__).resolve().read_bytes()))
    out["files"] = list(names)

    try:
        commit = _git(["rev-parse", "HEAD"], cwd=here, git=git).strip()
        # --porcelain reports untracked files too, and that is wanted: a scoring path
        # holding a module git has never seen is not a clean tree, it is a tree nobody
        # else can reproduce.
        status = _git(["status", "--porcelain", "--", *names], cwd=here, git=git)
    except OSError as exc:
        out.update({"commit": None, "dirty": None, "git_status": f"unavailable: {exc}"})
        return out

    changed = sorted(line[3:].strip() for line in status.splitlines() if line.strip())
    out["commit"] = commit
    out["dirty"] = bool(changed)
    out["git_status"] = ("clean" if not changed else
                         "uncommitted changes in " + ", ".join(changed))
    return out


def load_hypotheses(hyp_dir: Path) -> dict[str, str]:
    hyps: dict[str, str] = {}
    for p in sorted(hyp_dir.glob("*.txt")):
        hyps[p.stem] = p.read_text(encoding="utf-8")
    for p in sorted(hyp_dir.glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                hyps[rec["item_id"]] = rec["text"]
    return hyps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hyp-dir", type=Path)
    ap.add_argument("--arm", default="")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--jobs", type=_jobs_arg, default=None,
                    help="worker processes (default: one per core, capped at the item "
                         "count). 1 runs serially in this process, which is the path to "
                         "use when a pooled figure needs corroborating -- the two must "
                         "agree byte for byte.")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.hyp_dir:
        ap.error("one of --hyp-dir or --self-test is required")

    manifest = {r["item_id"]: r for r in json.loads(C.MANIFEST.read_text())} \
        if C.MANIFEST.exists() else {}
    hyps = load_hypotheses(args.hyp_dir)
    if not hyps:
        print(f"no hypotheses found in {args.hyp_dir}")
        return 1

    # Every read happens here, before any scoring. Two reasons, and the second is the one
    # that matters: no worker process ever touches the filesystem, so nothing about the
    # result can depend on how many of them there are; and the task list fixes the output
    # order before a single item is dispatched.
    tasks: list[tuple] = []
    for dpath in sorted(C.DIALOGUE_DIR.glob("ASR-*.json")):
        dlg = json.loads(dpath.read_text(encoding="utf-8"))
        item = dlg["item_id"]
        if item not in hyps:
            print(f"  (no hypothesis for {item} -- skipped, NOT scored as zero)")
            continue
        ref = (C.GROUND_TRUTH_DIR / f"{item}.txt").read_text(encoding="utf-8")
        ents = json.loads((C.GROUND_TRUTH_DIR / f"{item}.entities.json").read_text(encoding="utf-8"))
        tlp = C.GROUND_TRUTH_DIR / f"{item}.timeline.json"
        tl = json.loads(tlp.read_text(encoding="utf-8")) if tlp.exists() else {"segments": []}
        dur = manifest.get(item, {}).get("duration_s", 300.0)
        tasks.append((item, ref, hyps[item], ents, tl, dur, dlg["family"]))

    jobs = resolve_jobs(args.jobs, len(tasks))
    if tasks:
        print(f"scoring {len(tasks)} items across {jobs} "
              f"{'process' if jobs == 1 else 'processes'}")
    rows = score_items(tasks, jobs)

    if not rows:
        print("nothing scored")
        return 1

    # Runaways are separated here, once, and every aggregate below runs over `rows`.
    # Keeping the split in one place is deliberate: an exclusion applied in some pooling
    # helpers and not others produces figures with different denominators that all look
    # like corpus rates.
    runaways = [r for r in rows if "runaway" in r]
    rows = [r for r in rows if "runaway" not in r]
    total_items = len(rows) + len(runaways)
    if not rows:
        print("every item was a runaway; nothing scoreable")
        for r in runaways:
            print(f"  {r['item_id']}  {r['runaway']['kind']}: {r['runaway']['detail']}")
        return 1

    excluded = f", {len(runaways)} EXCLUDED as runaways" if runaways else ""
    print(f"\narm: {args.arm or args.hyp_dir.name}   scored {len(rows)} calls{excluded}\n")
    print(f"{'item':9s} {'family':19s} {'CER':>7s} {'CERn':>7s} {'CER-ns':>7s} "
          f"{'WER':>7s} {'WERn':>7s} {'WER-ns':>7s} {'ENT':>7s} {'ins/min':>8s}")
    for r in rows:
        ent = r["entity"]["accuracy"]
        nan = float('nan')
        print(f"{r['item_id']:9s} {r['family']:19s} "
              f"{r['cer_raw']:7.4f} {r['cer_norm']:7.4f} "
              f"{(r.get('cer_nospace') if r.get('cer_nospace') is not None else nan):7.4f} "
              f"{(r['wer_raw'] if r['wer_raw'] is not None else nan):7.4f} "
              f"{(r['wer_norm'] if r['wer_norm'] is not None else nan):7.4f} "
              f"{(r.get('wer_nospace') if r.get('wer_nospace') is not None else nan):7.4f} "
              f"{(ent if ent is not None else nan):7.3f} "
              f"{(r['insertions_per_nonspeech_min'] or 0):8.1f}")

    # Aggregate the way an ASR result should be aggregated: pooled over the corpus, not as
    # a mean of per-file rates. A mean of rates weights a 3-minute call the same as a
    # 10-minute one and is not the corpus error rate.
    def pooled(prefix: str) -> float | None:
        """Corpus error rate: total errors / total reference units.

        The denominator must match the prefix. An earlier version divided BOTH wer_raw and
        wer_norm by `ref_words_norm`; normalisation changes the token count (it collapses
        whitespace and rewrites numerals), so wer_raw was being divided by the wrong total
        and reported slightly low.
        """
        kind, label = prefix.split("_", 1)
        num = sum(sum(r[f"{prefix}_sdi"]) for r in rows if r.get(f"{prefix}_sdi"))
        if kind == "cer":
            den = sum(r.get(f"ref_chars_{label}", r["ref_chars"]) for r in rows)
        else:
            den = sum(r.get(f"ref_words_{label}", 0) for r in rows)
        return num / den if den else None

    overall = {k: pooled(k) for k in ("cer_raw", "cer_norm", "wer_raw", "wer_norm")}

    def pooled_diag(err_key: str, den_key: str) -> float | None:
        den = sum(r.get(den_key) or 0 for r in rows)
        return (sum(r.get(err_key) or 0 for r in rows) / den) if den else None

    overall["cer_nospace"] = pooled_diag("cer_nospace_errors", "ref_chars_nospace")
    overall["wer_nospace"] = pooled_diag("wer_nospace_errors", "ref_words_nospace")

    # The runaway rate travels WITH the pooled error rate, never after it. An arm that
    # loops on 1 call in 20 and transcribes the other 19 well is not a good arm, and a CER
    # quoted without this number says it is.
    overall["scoreable_items"] = len(rows)
    overall["runaway_items"] = len(runaways)
    overall["runaway_rate"] = len(runaways) / total_items if total_items else None
    print(f"\n{'POOLED':9s} {'':19s} "
          f"{overall['cer_raw']:7.4f} {overall['cer_norm']:7.4f} "
          f"{overall['cer_nospace']:7.4f} "
          f"{overall['wer_raw']:7.4f} {overall['wer_norm']:7.4f} "
          f"{overall['wer_nospace']:7.4f}")

    if runaways:
        print(f"\nRUNAWAY -- {len(runaways)} of {total_items} items "
              f"({overall['runaway_rate'] * 100:.1f}%) excluded from every figure above.")
        for r in runaways:
            print(f"  {r['item_id']:9s} {r['family']:19s} {r['runaway']['kind']:11s} "
                  f"{r['runaway']['detail']}")
        print("  Decoder failures, not transcription errors. Excluded because pooling them")
        print("  gives a corpus rate that describes no call in the set -- NOT because they")
        print("  are forgiven. The rate above is the cost of the exclusion; quote it too.")
    print("  CER-ns / WER-ns ignore whitespace on BOTH sides. Diagnostic only: the contract "
          "metric is CERn.\n  Thai has no inter-word spaces, so the gap between CERn and "
          "CER-ns is convention, not mishearing.")

    fams: dict[str, list[dict]] = {}
    for r in rows:
        fams.setdefault(r["family"], []).append(r)
    by_family: dict[str, dict] = {}
    for fam in sorted(fams):
        rs = fams[fam]
        den = sum(r.get("ref_chars_norm", r["ref_chars"]) for r in rs)
        et = sum(r["entity"]["total"] for r in rs)
        den_ns = sum(r.get("ref_chars_nospace") or 0 for r in rs)
        by_family[fam] = {
            "items": len(rs),
            "cer_norm": (sum(sum(r["cer_norm_sdi"]) for r in rs) / den) if den else None,
            "cer_nospace": (sum(r.get("cer_nospace_errors") or 0 for r in rs) / den_ns)
                           if den_ns else None,
            "entity_hit": sum(r["entity"]["hit"] for r in rs),
            "entity_total": et,
        }

    print("\nBY FAMILY (pooled CER after normalisation)")
    for fam, f in by_family.items():
        eh, et = f["entity_hit"], f["entity_total"]
        print(f"  {fam:20s} CER {f['cer_norm']:.4f}   entity {eh}/{et}"
              f"{'  ' + f'({eh / et:.1%})' if et else ''}")

    by_type: dict[str, dict] = {}
    for r in rows:
        for etype, v in r["entity"]["per_type"].items():
            a = by_type.setdefault(etype, {"total": 0, "hit": 0, "surface": 0, "value": 0})
            for k in ("total", "hit", "surface", "value"):
                a[k] += v[k]
    by_type = {k: by_type[k] for k in sorted(by_type)}

    print("\nENTITY ACCURACY BY TYPE")
    for etype, a in by_type.items():
        print(f"  {etype:10s} {a['hit']:4d}/{a['total']:<4d} ({a['hit'] / a['total']:6.1%})"
              f"   surface={a['surface']} value={a['value']}")

    ins = [r["insertions_per_nonspeech_min"] for r in rows
           if r["insertions_per_nonspeech_min"] is not None]
    ent_hit = sum(r["entity"]["hit"] for r in rows)
    ent_total = sum(r["entity"]["total"] for r in rows)

    if args.json:
        # Provenance, so a published figure traces back to the bytes that produced it.
        # `arm` alone defaults to "" and would leave the file anonymous.
        gt = sorted(C.GROUND_TRUTH_DIR.glob("ASR-*.txt"))
        gt_digest = (hashlib.sha256(b"".join(g.read_bytes() for g in gt)).hexdigest()
                     if gt else None)
        prov = code_provenance()
        out = {
            "arm": args.arm,
            "generated_at": datetime.datetime.now(datetime.timezone.utc)
                                    .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hyp_dir": str(args.hyp_dir),
            "items_scored": len(rows),
            "ground_truth_files": len(gt),
            "ground_truth_sha256": gt_digest,
            # Kept at top level, under its original name and its original meaning -- the
            # bytes on disk. The two 2026-08-17 reports carry it here, and redefining what
            # it measures would quietly break any comparison against them. Everything that
            # one hash could not answer sits beside it in `scoring_code`.
            "scoring_code_sha256": prov["sha256"],
            "scoring_code": prov,
            "jobs": jobs,
            "normalisation": ("asr_common.normalise_thai -- NFC, zero-width, doubled SARA E, "
                              "Thai digits, whitespace"),
            "contract_metric": "cer_norm",
            "diagnostic_metrics": {
                "cer_nospace": ("whitespace removed from both sides. NOT the contract metric: "
                                "ASR-EXPECTATION.md class 2 rules word spaces hard_miss, and "
                                "stripping them in normalisation would corrupt the "
                                "amount-beside-unit span entity scoring depends on. Reported "
                                "because Thai has no inter-word spaces, so the gap between "
                                "cer_norm and cer_nospace is authoring convention."),
                "wer_nospace": ("both sides unspaced before tokenising, so the tokeniser does "
                                "the same job on each"),
            },
            # Pooled over the corpus, never averaged over per-file rates. See pooled().
            "overall": overall,
            "runaways": [
                {"item_id": r["item_id"], "family": r["family"],
                 "ref_chars": r["ref_chars"], "hyp_chars": r["hyp_chars"], **r["runaway"]}
                for r in runaways
            ],
            "entity_overall": {
                "hit": ent_hit, "total": ent_total,
                "accuracy": (ent_hit / ent_total) if ent_total else None,
            },
            "insertions_per_nonspeech_min": {
                "measurable_items": len(ins),
                "mean": (sum(ins) / len(ins)) if ins else None,
                "max": max(ins) if ins else None,
            },
            "by_family": by_family,
            "entity_by_type": by_type,
            "items": rows,
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
