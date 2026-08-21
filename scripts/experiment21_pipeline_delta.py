"""Experiment 21 -- the pipeline delta: does the machine transcript change the final answer?

One labeller, one prompt, one schema, run over several versions of the SAME 20 calls that
differ only in how the text got there. Count on how many calls -- and on which fields -- the
final JSON changes. No arm is scored as "right": a labeller that is reliably wrong agrees
with itself 100%, so every number here is AGREEMENT, never accuracy, and the report says so.

THE SIX ARMS
    ceiling         exact reference transcript          -> Qwen3.8-27B
    format-control  reference mechanically degraded to  -> Qwen3.8-27B
                    ASR-like format, ZERO transcription
                    error (turn structure flattened,
                    entities spoken->digit form)
    qwen-pipeline   Qwen3-ASR-1.7B transcript           -> Qwen3.8-27B   (the candidate)
    gemini-asr-text Gemini's ASR transcript             -> Qwen3.8-27B
    gemini-audio    the audio itself, one call          -> Gemini 2.5 Flash  (production)
    ceiling-gemini  exact reference transcript          -> Gemini 2.5 Flash

The format-control arm is what keeps this honest. This project has retracted two headlines
already because formatting conventions (number rendering, entity-match spacing) were read as
model quality. Arm 2 contains every formatting difference and no transcription error, so any
ceiling->ASR delta NOT exceeded by arm 2 is formatting, not hearing.

CONTROLS, AS REFUSALS
  * input provenance: sha256 of every input text recorded before the first call; scoring
    refuses if a hash changed since the run log was written.
  * noise floor per arm, in-session: 3 replicates per arm; a between-arm difference on a
    field where either arm's own replicates disagree is NOISE, never CHANGED.
  * two populations, never pooled: 6 retention-shaped calls (retention/downsell/mnp) and 14
    others are reported in separate tables; no pooled 20-call figure exists.
  * parse failures are recorded and scored as PARSE_FAILED, never dropped and never counted
    as agreement (the E20 skeleton trap must not recur here).

WHY THIS RUNS OUTSIDE `evalgen compare`: a label-free pack cannot pass preflight (no gt, the
asr-eval call ids fail CALL_ID_PATTERN, evidence-span rules assume committed transcripts).
Everything that CAN be reused verbatim is: the assembled prompt (v9_16_base, same sha as
every text eval), the retention schema through the same decoding transform, and the same
pinned decoding.

THE AUDIO ARM'S PROMPT. v9_16_base is production's prompt with six audio->transcript wording
repairs applied (prompts.AUDIO_TO_TRANSCRIPT), because the harness sends text. The audio arm
sends actual audio, so for it the six pairs are REVERSED -- mechanically, each exactly once,
refusing otherwise -- recovering production's own audio wording. Both prompt shas go in the
run log.

Run (under .venv, PYTHONPATH=src):
    python scripts/experiment21_pipeline_delta.py --dry-run       # inputs + parity table only
    python scripts/experiment21_pipeline_delta.py --smoke         # 2 calls x 6 arms x 1 rep
    python scripts/experiment21_pipeline_delta.py                 # the full 360
    python scripts/experiment21_pipeline_delta.py --score out/runs/<ts>-e21
"""

from __future__ import annotations

import argparse
import base64
import collections
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import httpx  # noqa: E402  (a dependency of the pinned openai client in .venv)

from evalgen.decoding import decoding_schema  # noqa: E402
from evalgen import prompts as P  # noqa: E402

# The pack root, overridable with ASR_EVAL_ROOT so a second corpus can be run without
# disturbing the frozen twenty-call set. Mirrors asr_common.ROOT, which does the same for
# the generator and scorer -- a runner that ignored the override would silently score a new
# corpus against the old corpus's ground truth, which is the worst kind of wrong.
ASR = Path(os.environ.get("ASR_EVAL_ROOT") or (REPO / "asr-eval"))
GT = ASR / "ground-truth"
DIALOGUES = ASR / "dialogues"
AUDIO = ASR / "audio"
HYPS = ASR / "hypotheses"
OUT = REPO / "out" / "runs"

CERT = REPO / "configs" / "token-factory.crt.pem"
TF_IP = "10.94.154.102"
TF_HOST = "token-fac-api.truecorp.co.th"
OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"

QWEN_LABELLER = "qwen3.8-27b-fp8"
GEMINI = "google/gemini-2.5-flash"

# Pinned decoding -- matches run_contract.decoding of every Token Factory text eval.
DECODING = {"temperature": 0.0, "top_p": 1.0, "seed": 0, "max_tokens": 8000}

RETENTION_SHAPED = {"retention", "downsell", "mnp"}

ARMS = ("ceiling", "format-control", "qwen-pipeline", "typhoon-pipeline",
        "gemini-asr-text", "gemini-audio", "ceiling-gemini")

# Which transcript set each text arm reads. ONE definition, deliberately.
#
# This map used to be written out twice -- once in `build_inputs` to load the text and once
# in `main` to record `input_paths` for the provenance/drift refusal. Two copies of a
# mapping from arm to transcript directory is the single worst place in this file for a
# copy-paste to diverge: the run would label arm A's calls with arm B's transcripts and
# every downstream number would be internally consistent and wrong. Adding the typhoon arm
# meant touching both copies, which is exactly when that happens, so they are now one.
HYP_DIRS = {
    "qwen-pipeline": "qwen3-asr-1.7b",
    "typhoon-pipeline": "typhoon-whisper-large-v3",
    "gemini-asr-text": "gemini-2.5-flash-audio",
}


class Refused(SystemExit):
    def __init__(self, msg: str) -> None:
        super().__init__(f"E21 REFUSING: {msg}")


def sha(text: str | bytes) -> str:
    b = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(b).hexdigest()


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    f = REPO / ".env"
    if f.is_file():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


# --------------------------------------------------------------------------------------
# Prompts and schema -- reused, never re-derived
# --------------------------------------------------------------------------------------


def text_prompt() -> P.Prompt:
    return P.PROMPTS["v9_16_base"]


def audio_prompt_text() -> str:
    """v9_16_base with the six audio->transcript repairs reversed, each exactly once.

    This recovers production's own audio wording for the one arm that actually sends
    audio. Refuses if any pair does not fire exactly once -- a partially reversed prompt
    is a prompt nobody reviewed.
    """
    text = text_prompt().system_text
    for before, after in P.AUDIO_TO_TRANSCRIPT:
        if text.count(after) != 1:
            raise Refused(f"audio-prompt reversal: {after[:40]!r} appears "
                          f"{text.count(after)} times, expected exactly 1")
        text = text.replace(after, before, 1)
    return text


def response_format() -> dict:
    schema = decoding_schema(json.loads(
        (REPO / "src" / "evalgen" / "schemas" / "retention.json").read_text(encoding="utf-8")))
    return {"type": "json_schema",
            "json_schema": {"name": schema.get("title", "analyze_call"), "schema": schema}}


# --------------------------------------------------------------------------------------
# Input construction
# --------------------------------------------------------------------------------------


def items() -> list[str]:
    """Every reference transcript in the pack, cross-checked against its own manifest.

    This used to assert `len(ids) == 20`, which was right when one pack existed and became
    a false alarm the moment a second one did -- it refused a complete 138-call corpus for
    the crime of not being the old corpus.

    The check it is replaced by is the one that was actually wanted: the transcripts must
    match the pack's OWN manifest. That still catches a half-generated pack, a pack whose
    audio and ground truth have drifted apart, and the wrong ASR_EVAL_ROOT -- while letting
    a legitimately larger corpus through. A hardcoded count cannot tell those apart.
    """
    ids = sorted(p.stem for p in GT.glob("ASR-*.txt"))
    if not ids:
        raise Refused(f"no reference transcripts under {GT}")
    manifest = ASR / "manifest.json"
    if manifest.is_file():
        declared = sorted(r["item_id"] for r in json.loads(
            manifest.read_text(encoding="utf-8")))
        if declared != ids:
            missing = sorted(set(declared) - set(ids))
            extra = sorted(set(ids) - set(declared))
            raise Refused(
                f"{ASR.name}: manifest declares {len(declared)} items, ground truth has "
                f"{len(ids)}. missing={missing[:5]} extra={extra[:5]}. The pack is "
                "half-built or its audio and transcripts have drifted apart; scoring it "
                "would compare arms over different item sets."
            )
    return ids


def scenario_of(item: str) -> str:
    return json.loads((DIALOGUES / f"{item}.json").read_text(encoding="utf-8"))["scenario"]


def build_format_control(item: str) -> str:
    """The reference content in ASR-like clothing, with zero transcription error.

    Mechanical, from committed artifacts only: entity spoken forms -> digit values using
    the turn-indexed pairs in *.entities.json (replaced once, within the recorded turn,
    refusing on a miss), then all turns joined into a single whitespace-collapsed blob so
    the turn structure matches what both ASR arms actually emit (20 newlines vs 2,072).
    """
    dlg = json.loads((DIALOGUES / f"{item}.json").read_text(encoding="utf-8"))
    texts = [t["text"] for t in dlg["turns"]]
    ents = json.loads((GT / f"{item}.entities.json").read_text(encoding="utf-8"))
    for e in ents:
        idx, spoken, value = e["turn_idx"], e["spoken"], str(e["value"])
        if idx >= len(texts) or spoken not in texts[idx]:
            raise Refused(f"{item}: entity {value!r} not found verbatim in turn {idx}; "
                          f"the format-control arm must be exact or not exist")
        texts[idx] = texts[idx].replace(spoken, value, 1)
    return re.sub(r"\s+", " ", " ".join(texts)).strip()


def build_inputs(wanted: tuple[str, ...] = ARMS,
                 only: tuple[str, ...] | None = None) -> dict[str, dict[str, str]]:
    """arm -> item -> input text. The audio arm maps item -> wav path (string).

    `wanted` restricts construction to the arms actually being run. Building every arm
    unconditionally made `--arms` a lie: a run of four arms still refused if the inputs for
    a fifth were absent, which is what happens on any pack that does not carry a Gemini
    transcript set. The refusals below are kept -- they just now fire for arms you asked for
    rather than for ones you did not.
    """

    # One wav per call, enforced.
    #
    # WHY THIS REFUSAL EXISTS. `synthesize.py` writes the MEASURED duration into the wav
    # filename, so re-rendering a call whose script changed produces a file with a NEW name
    # beside the old one. On 2026-08-20 a corpus regeneration left the pack holding 185 wavs
    # for 138 calls -- 47 calls with two each, differing only in the duration field.
    #
    # Every consumer maps wav -> call by the phone token, and a dict built from `glob` keeps
    # whichever file the filesystem happened to return last. So an arm could transcribe
    # YESTERDAY's audio while being scored against TODAY's labels, and nothing downstream could
    # detect it: the transcript would be a faithful transcription of the wrong words, the counts
    # would be right, and the number would be wrong.
    #
    # Refusing is the only safe response -- picking the newest would silently paper over a pack
    # that is in an unexpected state.
    _by_phone_files: dict[str, list[str]] = {}
    for _w in AUDIO.glob("*.wav"):
        _by_phone_files.setdefault(_w.stem.split("_")[1], []).append(_w.name)
    _dupes = {k: v for k, v in _by_phone_files.items() if len(v) > 1}
    if _dupes:
        _first = sorted(_dupes.items())[0]
        raise Refused(
            f"{len(_dupes)} call(s) have more than one .wav in {AUDIO}. A stale "
            f"render was left beside a fresh one, and the wav->call map would pick "
            f"whichever the filesystem returned last -- transcribing old audio against new "
            f"labels. Example: phone {_first[0]} has {_first[1]}. Remove the stale files "
            f"before running."
        )

    wavs = {p.stem.split("_")[1]: p for p in AUDIO.glob("*.wav")}
    by_phone = {}
    for d in sorted(DIALOGUES.glob("ASR-*.json")):
        o = json.loads(d.read_text(encoding="utf-8"))
        by_phone[o["item_id"]] = o["meta"]["phone_number"]

    inputs: dict[str, dict[str, str]] = {a: {} for a in wanted}
    # `only` restricts to an explicit item list. Without it build_inputs re-derived the
    # full set from ground truth and --items had no effect here, so a run deliberately
    # scoped around a known-bad item still refused on that item.
    for it in (list(only) if only else items()):
        ref = (GT / f"{it}.txt").read_text(encoding="utf-8").strip()
        for arm in ("ceiling", "ceiling-gemini"):
            if arm in inputs:
                inputs[arm][it] = ref
        if "format-control" in inputs:
            inputs["format-control"][it] = build_format_control(it)
        for arm, hyp_dir in HYP_DIRS.items():
            if arm not in inputs:
                continue
            f = HYPS / hyp_dir / f"{it}.txt"
            if not f.is_file():
                raise Refused(f"{arm}: missing hypothesis {f}. Transcripts are frozen "
                              f"inputs; re-create them before running.")
            inputs[arm][it] = f.read_text(encoding="utf-8").strip()
        if "gemini-audio" in inputs:
            wav = wavs.get(by_phone[it])
            if wav is None:
                raise Refused(f"gemini-audio: no wav for {it}")
            inputs["gemini-audio"][it] = str(wav)
    return inputs


def parity_stats(text: str) -> dict:
    ns = sum(1 for c in text if not c.isspace())
    return {"chars": len(text), "digits": sum(c.isdigit() for c in text),
            "newlines": text.count("\n"),
            "spaces_per_100": round(100 * text.count(" ") / ns, 1) if ns else 0.0}


# --------------------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------------------


def tf_client(key: str) -> httpx.Client:
    return httpx.Client(verify=str(CERT), timeout=300.0,
                        headers={"Authorization": f"Bearer {key}"})


def call_labeller(client: httpx.Client | None, env: dict, arm: str, system: str,
                  user_text: str | None, wav_path: str | None, rep: int) -> dict:
    """One label call. Returns a record; never raises for a model-side failure."""
    rec: dict = {"arm": arm, "replicate": rep, "requested_at": now()}
    if arm in ("gemini-audio", "ceiling-gemini"):
        url, key, model = OPENROUTER, env.get("OPENROUTER_API_KEY", ""), GEMINI
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if arm == "gemini-audio":
            b64 = base64.b64encode(Path(wav_path).read_bytes()).decode("ascii")
            # NO FRAMING TEXT PART. This used to send `{"type": "text", "text": "."}` ahead
            # of the audio -- a token no other arm sent and no document mentioned. It was
            # the only content difference between the audio arm and the six text arms, which
            # made "all models ran identically" untrue in a way nobody could see from the
            # log. `prompts.build_messages` states the principle: any word added around the
            # input is a word this harness invented and then scored a model on.
            content = [{"type": "input_audio",
                        "input_audio": {"data": b64, "format": "wav"}}]
            # The user turn in production carries the audio; the prompt is the system turn.
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": content}]
        else:
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": user_text}]
        payload = {"model": model, "messages": messages,
                   "response_format": response_format(), **DECODING,
                   # THE THREE OPENROUTER GUARDS THE MAIN HARNESS TREATS AS MANDATORY, and
                   # which this script omitted for both Gemini arms. `request.py:22-26` is
                   # explicit about the cost of the middle one: "OpenRouter DROPS a parameter
                   # an endpoint does not support rather than failing, so without it an arm
                   # rerouted mid-run to an endpoint with no structured-output support falls
                   # back to prompt-coaxing, and the run log looks identical."
                   #
                   # Measured before adding it: 414/414 Gemini calls parsed and not one
                   # produced an out-of-enum value, so the guard was not silently rescuing a
                   # broken run. It is here so that stays checkable rather than lucky.
                   #
                   # `reasoning.effort` matters for a second reason: without it Gemini ran at
                   # provider default while retention-e23.plan.json:311 preregisters "none".
                   "usage": {"include": True},
                   "provider": {"require_parameters": True},
                   "reasoning": {"effort": "none"}}
        # seed is unsupported on some OpenRouter providers; harmless if ignored.
        sender = lambda: httpx.post(url, headers=headers, json=payload, timeout=300.0)  # noqa: E731
    else:
        model = QWEN_LABELLER
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user_text}]
        payload = {"model": model, "messages": messages,
                   "response_format": response_format(), **DECODING}
        sender = lambda: client.post(  # noqa: E731
            f"https://{TF_IP}/v1/chat/completions", headers={"Host": TF_HOST},
            json=payload, extensions={"sni_hostname": TF_HOST})

    rec["model"] = model
    last = ""
    for attempt in range(3):
        t0 = time.time()
        try:
            r = sender()
            rec["latency_s"] = round(time.time() - t0, 2)
            if r.status_code != 200:
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                time.sleep(2 * (attempt + 1))
                continue
            body = r.json()
            content = body["choices"][0]["message"]["content"]
            rec["usage"] = body.get("usage")
            # WHO ACTUALLY ANSWERED. The record stored the model we ASKED for and nothing
            # about what replied, so model identity was never verified on any E21/E23 call.
            # The main harness captures all four of these (client.py:291-310) because a
            # 2026-08-04 run was silently served by two different backends under one model
            # id, and nothing in the log could show it.
            rec["observed_model"] = body.get("model")
            rec["provider"] = body.get("provider")
            rec["finish_reason"] = (body.get("choices") or [{}])[0].get("finish_reason")
            try:
                parsed = json.loads(content)
                missing = [k for k in ("product", "call_event_detection", "recommendation")
                           if k not in parsed]
                if missing:
                    rec.update(status="parse_failed",
                               error=f"missing required keys {missing}",
                               raw_head=content[:300])
                else:
                    rec.update(status="ok", response=parsed)
            except json.JSONDecodeError as exc:
                rec.update(status="parse_failed", error=str(exc), raw_head=content[:300])
            return rec
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {str(exc)[:200]}"
            time.sleep(2 * (attempt + 1))
    rec.update(status="transport_failed", error=last)
    return rec


# --------------------------------------------------------------------------------------
# Field extraction and scoring
# --------------------------------------------------------------------------------------


def extract_fields(resp: dict) -> dict:
    """The three comparable dimensions, mirroring the harness's own grain."""
    prods = resp.get("product") or {}
    out = {"products": sorted(prods.keys())}
    for pkey, pv in sorted(prods.items()):
        if not isinstance(pv, dict):
            out[f"{pkey}.outcome"] = "MALFORMED"
            continue
        out[f"{pkey}.outcome"] = pv.get("retention_outcome", "")
        reasons = []
        for slot in ("main", "secondary", "third"):
            rv = ((pv.get(slot) or {}).get("reason") or "").strip()
            if rv:
                reasons.append(rv)
        out[f"{pkey}.reasons"] = sorted(set(reasons))
    return out


def modal(values: list) -> tuple[str | None, bool]:
    """(mode as canonical json, unanimous?) -- None mode when all replicates differ."""
    keys = [json.dumps(v, sort_keys=True, ensure_ascii=False) for v in values]
    counts = collections.Counter(keys)
    top, n = counts.most_common(1)[0]
    if n == 1 and len(keys) > 1:
        return None, False
    return top, n == len(keys)


def score(run_dir: Path) -> dict:
    log = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    # Provenance refusal: inputs on disk must still match the hashes in the log.
    for arm, per_item in log["input_sha256"].items():
        if arm == "gemini-audio":
            continue
        for it, want in per_item.items():
            src = log["input_paths"][arm].get(it)
            if src and Path(src).is_file():
                got = sha(Path(src).read_text(encoding="utf-8").strip())
                if got != want:
                    raise Refused(f"input drift: {arm}/{it} hash changed since the run log "
                                  f"was written. Nothing scored.")

    rows = [json.loads(l) for l in (run_dir / "results.jsonl")
            .read_text(encoding="utf-8").splitlines() if l.strip()]
    by = collections.defaultdict(list)
    for r in rows:
        by[(r["arm"], r["item_id"])].append(r)

    reps = log["replicates"]
    arms_run = sorted({r["arm"] for r in rows})
    items_run = sorted({r["item_id"] for r in rows})
    for arm in arms_run:
        for it in items_run:
            have = by.get((arm, it), [])
            if len(have) != reps:
                raise Refused(f"{arm}/{it}: {len(have)} replicates, expected {reps}. "
                              f"A partial arm cannot be scored against a complete one.")

    # Per arm/item: field -> (mode, unanimous). parse/transport failures poison the item.
    fields: dict = {}
    failures: dict = collections.defaultdict(list)
    for (arm, it), recs in by.items():
        bad = [r for r in recs if r["status"] != "ok"]
        if bad:
            failures[arm].append({"item": it, "statuses": [r["status"] for r in recs]})
            fields[(arm, it)] = None
            continue
        per_rep = [extract_fields(r["response"]) for r in recs]
        keys = sorted({k for f in per_rep for k in f})
        fields[(arm, it)] = {
            k: modal([f.get(k, "__ABSENT__") for f in per_rep]) for k in keys}

    def compare(arm_a: str, arm_b: str) -> dict:
        """Field verdicts between two arms, split by population."""
        pops = {"retention_shaped": [], "other": []}
        for it in items_run:
            pop = ("retention_shaped" if scenario_of(it) in RETENTION_SHAPED else "other")
            fa, fb = fields.get((arm_a, it)), fields.get((arm_b, it))
            if fa is None or fb is None:
                pops[pop].append({"item": it, "verdict": "PARSE_FAILED"})
                continue
            per_field = {}
            for k in sorted(set(fa) | set(fb)):
                ma, ua = fa.get(k, ("__ABSENT__", True))
                mb, ub = fb.get(k, ("__ABSENT__", True))
                if ma is None or mb is None or not ua or not ub:
                    per_field[k] = "NOISE"       # within-arm disagreement swamps the delta
                elif ma == mb:
                    per_field[k] = "SAME"
                else:
                    per_field[k] = "CHANGED"
            verdict = ("CHANGED" if any(v == "CHANGED" for v in per_field.values())
                       else "NOISE" if any(v == "NOISE" for v in per_field.values())
                       else "SAME")
            pops[pop].append({"item": it, "verdict": verdict, "fields": per_field})
        return pops

    pairs = {}
    want = [("ceiling", "format-control"), ("ceiling", "qwen-pipeline"),
            ("ceiling", "typhoon-pipeline"), ("ceiling", "gemini-asr-text"),
            ("ceiling-gemini", "gemini-audio"),
            ("qwen-pipeline", "gemini-audio"),
            ("typhoon-pipeline", "gemini-audio"),
            ("qwen-pipeline", "typhoon-pipeline")]
    for a, b in want:
        if a in arms_run and b in arms_run:
            pairs[f"{a}|{b}"] = compare(a, b)

    # Within-arm noise floor: fraction of fields where replicates were not unanimous.
    noise = {}
    for arm in arms_run:
        total = shaky = 0
        for it in items_run:
            f = fields.get((arm, it))
            if not f:
                continue
            for k, (_m, unanimous) in f.items():
                total += 1
                shaky += (not unanimous)
        noise[arm] = {"fields": total, "not_unanimous": shaky,
                      "rate": round(shaky / total, 4) if total else None}

    return {"run": run_dir.name, "scored_at": now(), "replicates": reps,
            "arms": arms_run, "items": items_run,
            "noise_floor": noise, "failures": dict(failures), "pairs": pairs}


# --------------------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(prog="experiment21")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--items", nargs="*", default=[])
    ap.add_argument("--replicates", type=int, default=3)
    ap.add_argument("--jobs", type=int, default=1,
                    help="concurrent in-flight calls. Default 1: the serial path that "
                         "produced every published E21 figure. Raise it for a new "
                         "experiment, where three hours of serial audio calls is the "
                         "difference between an eval you can iterate on and one you can "
                         "afford to run once.")
    ap.add_argument("--arms", nargs="*", default=list(ARMS))
    ap.add_argument("--score", type=Path, default=None)
    ap.add_argument("--resume", type=Path, default=None,
                    help="existing e21 run dir: skip records already present")
    args = ap.parse_args()

    if args.score:
        result = score(args.score)
        out = args.score / "agreement.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {out}")
        for pair, pops in result["pairs"].items():
            for pop, rows in pops.items():
                c = collections.Counter(r["verdict"] for r in rows)
                print(f"  {pair:34} {pop:17} {dict(c)}")
        print(f"  noise floors: " + ", ".join(
            f"{a}={v['rate']}" for a, v in result["noise_floor"].items()))
        return 0

    env = load_env()
    # An unknown --arms value used to be dropped in silence by this filter: ask for
    # `typhoon-pipline` and you got a clean run of nothing, exit 0, and a results file that
    # looked fine until someone counted the rows. Name it instead.
    unknown = sorted(set(args.arms) - set(ARMS))
    if unknown:
        raise Refused(f"unknown arm(s) {unknown}; known arms are {list(ARMS)}. "
                      "A misspelled arm silently running nothing is worse than a failure.")
    run_arms = [a for a in ARMS if a in args.arms]
    run_items = args.items or (
        ["ASR-001", "ASR-011"] if args.smoke else items())
    inputs = build_inputs(tuple(run_arms), tuple(run_items))
    sys_text = text_prompt()
    audio_sys = audio_prompt_text()

    # ---- dry-run output: shas + the format-parity table, before any model call --------
    input_sha = {arm: {} for arm in run_arms}
    input_paths = {arm: {} for arm in run_arms}
    print(f"{'arm':16} {'digits':>7} {'newlines':>9} {'spaces/100':>11}   (medians over 20)")
    for arm in run_arms:
        stats = []
        for it, val in inputs[arm].items():
            if arm == "gemini-audio":
                input_sha[arm][it] = sha(Path(val).read_bytes())
                input_paths[arm][it] = val
            else:
                input_sha[arm][it] = sha(val)
                stats.append(parity_stats(val))
                # ceiling/gemini share GT paths; hypotheses have real paths
                input_paths[arm][it] = ""
        if stats:
            med = lambda k: sorted(s[k] for s in stats)[len(stats) // 2]  # noqa: E731
            print(f"{arm:16} {med('digits'):>7} {med('newlines'):>9} "
                  f"{med('spaces_per_100'):>11}")
    # Only for arms actually being run: these dicts are keyed on `inputs`, which no longer
    # carries every arm unconditionally.
    for arm, hyp_dir in HYP_DIRS.items():
        if arm in inputs:
            input_paths[arm] = {it: str(HYPS / hyp_dir / f"{it}.txt")
                                for it in inputs[arm]}
    print(f"\nprompt v9_16_base sha {sys_text.sha[:16]}...  "
          f"audio-reversed sha {sha(audio_sys)[:16]}...")
    if args.dry_run:
        print("dry run: no model calls made.")
        return 0

    reps = 1 if args.smoke else args.replicates

    if args.resume:
        run_dir = args.resume
        done = set()
        f = run_dir / "results.jsonl"
        if f.is_file():
            for l in f.read_text(encoding="utf-8").splitlines():
                if l.strip():
                    r = json.loads(l)
                    done.add((r["arm"], r["item_id"], r["replicate"]))
        print(f"resuming {run_dir.name}: {len(done)} records already present")
    else:
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        run_dir = OUT / f"{stamp}-e21{'-smoke' if args.smoke else ''}"
        run_dir.mkdir(parents=True, exist_ok=False)
        done = set()
        (run_dir / "run.json").write_text(json.dumps({
            "experiment": "e21-pipeline-delta", "created_at": now(),
            "prompt_id": "v9_16_base", "prompt_sha": sys_text.sha,
            "audio_prompt_sha": sha(audio_sys),
            "labeller_internal": QWEN_LABELLER, "labeller_incumbent": GEMINI,
            "decoding": DECODING, "replicates": reps, "arms": run_arms,
            "items": run_items, "input_sha256": input_sha, "input_paths": input_paths,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    tf = tf_client(env.get("TOKEN_FACTORY_API_KEY", ""))
    results = (run_dir / "results.jsonl").open("a", encoding="utf-8")
    total = sum(1 for a in run_arms for i in run_items for r in range(reps)
                if (a, i, r) not in done)
    n = 0
    t_start = time.time()
    # The work list is materialised before anything runs, so --jobs can change how many
    # calls are in flight but never WHICH calls are made. Resume is applied here, once.
    work = [
        (arm, it, rep,
         audio_sys if arm == "gemini-audio" else sys_text.system_text,
         inputs[arm][it])
        for arm in run_arms
        for it in run_items
        for rep in range(reps)
        if (arm, it, rep) not in done
    ]

    def run_one(job):
        arm, it, rep, system, val = job
        rec = call_labeller(
            tf if arm not in ("gemini-audio", "ceiling-gemini") else None,
            env, arm, system,
            None if arm == "gemini-audio" else val,
            val if arm == "gemini-audio" else None, rep)
        rec["item_id"] = it
        return rec

    if args.jobs <= 1:
        # The serial path is kept, and kept as the DEFAULT, because every published E21
        # figure came from it. Concurrency is opt-in so re-running that experiment
        # reproduces byte for byte rather than merely closely.
        for job in work:
            n += 1
            rec = run_one(job)
            results.write(json.dumps(rec, ensure_ascii=False) + "\n")
            results.flush()
            print(f"[{n}/{total}] {job[0]:16} {job[1]} r{job[2]} {rec['status']:16} "
                  f"{rec.get('latency_s', '-')}s", flush=True)
    else:
        # Concurrency is a WALL-CLOCK change only. Every record carries its own arm, item
        # and replicate, and `score()` reads results.jsonl by those keys rather than by
        # position, so completion order cannot affect any number. What it does affect is
        # affordability: at one call in flight, 414 audio calls is three hours, which makes
        # a re-run a day's decision instead of a coffee break.
        #
        # One append-mode handle is shared by every worker, so writes go under a lock.
        # Without it two records interleave mid-line and the file stops being valid JSONL --
        # a corruption that would look like a model failure when scoring later refused it.
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed

        write_lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(run_one, job): job for job in work}
            for fut in as_completed(futures):
                job = futures[fut]
                rec = fut.result()
                with write_lock:
                    n += 1
                    results.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    results.flush()
                    print(f"[{n}/{total}] {job[0]:16} {job[1]} r{job[2]} "
                          f"{rec['status']:16} {rec.get('latency_s', '-')}s", flush=True)
    results.close()
    print(f"\n{run_dir}  ({time.time() - t_start:.0f}s)")
    print(f"score with: python scripts/experiment21_pipeline_delta.py --score {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
