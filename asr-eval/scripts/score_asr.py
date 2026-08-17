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
    python asr-eval/scripts/score_asr.py --self-test
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
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
    nh_nums = strip_thousands(hyp)          # same text, thousands separators removed
    runs = numeric_runs(hyp) | set(spoken_digit_runs(hyp))

    per_type: dict[str, dict] = {}
    details = []
    for e in entities:
        etype, value, spoken = e["type"], e["value"], e["spoken"]
        surface = C.normalise_thai(spoken) in nh
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


def score_item(item: str, ref: str, hyp: str, entities: list[dict],
               timeline: dict, duration_s: float, family: str) -> dict:
    ref_flat = " ".join(ref.split())
    hyp_flat = " ".join(hyp.split())

    out: dict = {"item_id": item, "family": family,
                 "ref_chars": len(chars(ref_flat)), "hyp_chars": len(chars(hyp_flat))}

    for label, r, h in (("raw", ref_flat, hyp_flat),
                        ("norm", C.normalise_thai(ref_flat), C.normalise_thai(hyp_flat))):
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

    out["entity"] = score_entities(entities, hyp_flat)

    ns = nonspeech_seconds(timeline, duration_s)
    out["nonspeech_s"] = round(ns, 2)
    ins_words = out.get("wer_norm_sdi", [0, 0, 0])[2]
    out["insertions_per_nonspeech_min"] = round(ins_words / (ns / 60), 2) if ns > 1 else None
    return out


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
        return text + " " + "ขอบคุณครับ ยินดีให้บริการครับ " * 40
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

    rows = []
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
        rows.append(score_item(item, ref, hyps[item], ents, tl, dur, dlg["family"]))

    if not rows:
        print("nothing scored")
        return 1

    print(f"\narm: {args.arm or args.hyp_dir.name}   scored {len(rows)}/20 calls\n")
    print(f"{'item':9s} {'family':19s} {'CER':>7s} {'CERn':>7s} {'WER':>7s} {'WERn':>7s} "
          f"{'ENT':>7s} {'ins/min':>8s}")
    for r in rows:
        ent = r["entity"]["accuracy"]
        print(f"{r['item_id']:9s} {r['family']:19s} "
              f"{r['cer_raw']:7.4f} {r['cer_norm']:7.4f} "
              f"{(r['wer_raw'] if r['wer_raw'] is not None else float('nan')):7.4f} "
              f"{(r['wer_norm'] if r['wer_norm'] is not None else float('nan')):7.4f} "
              f"{(ent if ent is not None else float('nan')):7.3f} "
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
    print(f"\n{'POOLED':9s} {'':19s} "
          f"{overall['cer_raw']:7.4f} {overall['cer_norm']:7.4f} "
          f"{overall['wer_raw']:7.4f} {overall['wer_norm']:7.4f}")

    fams: dict[str, list[dict]] = {}
    for r in rows:
        fams.setdefault(r["family"], []).append(r)
    by_family: dict[str, dict] = {}
    for fam in sorted(fams):
        rs = fams[fam]
        den = sum(r.get("ref_chars_norm", r["ref_chars"]) for r in rs)
        et = sum(r["entity"]["total"] for r in rs)
        by_family[fam] = {
            "items": len(rs),
            "cer_norm": (sum(sum(r["cer_norm_sdi"]) for r in rs) / den) if den else None,
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
        out = {
            "arm": args.arm,
            "generated_at": datetime.datetime.now(datetime.timezone.utc)
                                    .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hyp_dir": str(args.hyp_dir),
            "items_scored": len(rows),
            "ground_truth_files": len(gt),
            "ground_truth_sha256": gt_digest,
            "scoring_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "normalisation": ("asr_common.normalise_thai -- NFC, zero-width, doubled SARA E, "
                              "Thai digits, whitespace"),
            # Pooled over the corpus, never averaged over per-file rates. See pooled().
            "overall": overall,
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
