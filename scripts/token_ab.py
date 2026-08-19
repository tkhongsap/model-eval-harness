"""Why does sentiment_qa spend 30-50k output tokens a call, and what actually reduces it?

    PYTHONPATH=src python scripts/token_ab.py --items 8 --cap-usd 12
    PYTHONPATH=src python scripts/token_ab.py --dry-run      # no calls, prints the plan

THE QUESTION, as raised by Tar: a sentiment_qa call returns 30,000-50,000 output tokens.
That is 100-200x what the retention task spends. The retention control is not an estimate --
it is measured, from 1,632 calls already on disk in `out/runs/20260819-125109Z-e21`: median
**245** completion tokens per call, and `completion_tokens_details.reasoning_tokens` of
**0**, because retention runs `thinkingBudget: 0`.

So the model is not the variable. The configuration is, and it differs in three ways
(`config/sentiment_qa/qa_pipeline_fact_check.yml` vs `config/model_setting/retention.yml`):

    field             sentiment_qa      retention
    thinkingBudget    -1 (unlimited)    0
    temperature       1                 0.0
    topP              1                 0
    output keys       118, all required 51

Two further drivers live in the PROMPT, not the config, and no config change touches them:
`system_prompt.txt:18-24` instructs "When evaluating, think: 1...5", and `:26` requires a
written Thai `reason` for each of 23 `service_quality` fields -- 23 free-text justifications
in the VISIBLE output, which no thinking budget can cap.

Worth recording because it is the cheapest fix available: the prompt contradicts itself. The
Thai sentence at `:26` says the reason must NOT be written step by step -- it must be
summarised directly -- immediately below the English block demanding five numbered reasoning
steps.

WHAT THIS MEASURES, AND WHAT IT CANNOT. Output tokens, reasoning tokens, cost, latency, and
whether the answer still parses and still carries the expected key count. It does NOT measure
accuracy, and no arrangement of this script could: **there is no sentiment_qa ground truth in
this repository.** The binding registry is retention-only (`src/evalgen/apps.py:260`) -- no
testset, no labels, no scorer, no label space. Anyone who wants "does capping thinking cost
accuracy" needs a labelled QA batch first, and that is a separate piece of work.

A SECOND LIMIT, equally important. Production runs on **Vertex**, which takes
`thinkingConfig.thinkingBudget`. This runs on **OpenRouter**, which has no such parameter --
its nearest lever is `reasoning.effort`. So the arms below are a faithful test of the PROMPT
and the SCHEMA, and a proxy for the budget. The one number that would settle the budget
question outright needs no experiment at all: a single real Vertex response's
`usageMetadata.thoughtsTokenCount` beside its `candidatesTokenCount`. Only the team running
the batch can pull it, and `docs/sentiment-qa-token-ask.md` drafts that request.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import statistics
import sys
import threading
import time
import zipfile
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
PROD = REPO / "production-reference" / "sentiment-voice-analysis-develop"
QA = PROD / "config" / "sentiment_qa"
SYSTEM_PROMPT = QA / "system_prompt" / "system_prompt.txt"
USER_CONFIG = QA / "system_prompt" / "user_config.xlsx"
PACK = REPO / "asr-eval-v2"
OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-2.5-flash"

# The block at system_prompt.txt:18-24. Matched on its heading and the five numbered lines
# so the removal arm cannot silently become a no-op if the file is reformatted.
THINK_BLOCK = re.compile(
    r"\*\*When evaluating, think:\*\*\s*(?:\n\s*\d\..*)+", re.MULTILINE)


class Refused(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"token_ab REFUSING: {message}")


def load_env() -> None:
    path = REPO / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def user_config_text() -> str:
    """The field-definition corpus that production injects as `{user_prompt}`.

    Included rather than stubbed. In production this is fetched from SharePoint with the
    committed workbook as fallback, and it is ~94k characters of Thai field definitions --
    roughly 30k input tokens. Leaving it out would make every arm cheaper and the result
    meaningless, because the model would be answering a different, much smaller question.
    """
    with zipfile.ZipFile(USER_CONFIG) as archive:
        shared = archive.read("xl/sharedStrings.xml").decode("utf-8")
    values = re.findall(r"<t[^>]*>(.*?)</t>", shared, re.S)
    if not values:
        raise Refused(f"no shared strings in {USER_CONFIG}; the field definitions are the "
                      "bulk of the real prompt and a run without them measures nothing")
    return "\n".join(values)


def build_prompts(strip_think: bool) -> str:
    raw = SYSTEM_PROMPT.read_text(encoding="utf-8")
    if strip_think:
        stripped = THINK_BLOCK.sub("", raw)
        if stripped == raw:
            raise Refused(
                "the 'When evaluating, think:' block was not found, so the strip-think arm "
                "would be identical to baseline and would silently report 'no effect'. "
                "Check system_prompt.txt:18-24.")
        raw = stripped
    return (raw.replace("{user_prompt}", user_config_text())
               .replace("{date}", "2026-08-20"))


# Four arms. Each changes exactly ONE thing against the baseline, because an arm that moves
# two levers cannot attribute the result to either.
ARMS = [
    {"id": "baseline", "strip_think": False,
     "body": {"temperature": 1, "top_p": 1, "reasoning": {"effort": "high"}},
     "why": "production's own regime: temperature 1, topP 1, unlimited thinking"},
    {"id": "reasoning-low", "strip_think": False,
     "body": {"temperature": 1, "top_p": 1, "reasoning": {"effort": "low"}},
     "why": "the thinkingBudget lever, as near as OpenRouter can express it"},
    {"id": "reasoning-off", "strip_think": False,
     "body": {"temperature": 1, "top_p": 1, "reasoning": {"enabled": False}},
     "why": "retention's thinkingBudget: 0 regime"},
    {"id": "temp0", "strip_think": False,
     "body": {"temperature": 0, "top_p": 0, "reasoning": {"effort": "high"}},
     "why": "retention's decoding, production's thinking"},
    {"id": "no-think-block", "strip_think": True,
     "body": {"temperature": 1, "top_p": 1, "reasoning": {"effort": "high"}},
     "why": "prompt surgery only: the 'think 1...5' block removed, config untouched"},
]


def transcripts(count: int) -> list[tuple[str, str]]:
    """(item id, Thai conversation text) from the corpus already on disk."""
    paths = sorted((PACK / "ground-truth").glob("ASR-*.txt"))
    if not paths:
        raise Refused(f"no reference transcripts under {PACK / 'ground-truth'}")
    return [(p.stem, p.read_text(encoding="utf-8")) for p in paths[:count]]


def count_keys(value, depth: int = 0) -> int:
    if depth > 12 or not isinstance(value, (dict, list)):
        return 0
    if isinstance(value, list):
        return sum(count_keys(v, depth + 1) for v in value)
    return len(value) + sum(count_keys(v, depth + 1) for v in value.values())


def call(client: httpx.Client, key: str, system: str, user: str, body: dict) -> dict:
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "max_tokens": 65535,
        "seed": 0,
        "usage": {"include": True},
        **body,
    }
    started = time.monotonic()
    response = client.post(
        OPENROUTER, json=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    latency = time.monotonic() - started
    if response.status_code != 200:
        return {"status": f"http_{response.status_code}",
                "detail": response.text[:300], "latency_s": round(latency, 2)}
    data = response.json()
    usage = data.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    text = (data["choices"][0]["message"].get("content") or "")
    try:
        parsed = json.loads(text)
        keys, ok = count_keys(parsed), True
    except Exception:
        keys, ok = 0, False
    return {
        "status": "ok",
        "latency_s": round(latency, 2),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        # A breakdown OF completion_tokens, never additive to it.
        "reasoning_tokens": details.get("reasoning_tokens"),
        "cost": usage.get("cost"),
        "chars": len(text),
        "json_ok": ok,
        "keys": keys,
        "finish_reason": data["choices"][0].get("finish_reason"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(prog="token_ab")
    ap.add_argument("--items", type=int, default=8)
    ap.add_argument("--cap-usd", type=float, default=12.0)
    ap.add_argument("--jobs", type=int, default=6,
                    help="concurrent calls; wall clock only, never which "
                         "calls are made")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", type=Path, default=REPO / "docs" / "reports" / "token-ab.json")
    args = ap.parse_args()

    load_env()
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key and not args.dry_run:
        raise Refused("no OPENROUTER_API_KEY in environment or .env")

    calls = transcripts(args.items)
    systems = {arm["id"]: build_prompts(arm["strip_think"]) for arm in ARMS}

    print(f"token_ab -- {MODEL} via OpenRouter")
    print(f"{len(ARMS)} arms x {len(calls)} calls = {len(ARMS) * len(calls)} calls, "
          f"cap ${args.cap_usd:.2f}")
    for arm in ARMS:
        print(f"  {arm['id']:16} {arm['why']}")
        print(f"  {'':16} system prompt {len(systems[arm['id']]):,} chars")
    if args.dry_run:
        print("\ndry run: no calls made")
        return 0

    # Concurrency changes wall clock only, never which calls are made: the work list is
    # materialised in full first, exactly as experiment21_pipeline_delta does. Measured
    # serially these take ~5.5 minutes each -- 30k input tokens and up to 13k of output --
    # so 60 calls would have run past morning.
    work = [(arm, item, text) for arm in ARMS for item, text in calls]
    spent = 0.0
    rows: list[dict] = []
    lock = threading.Lock()
    stop = threading.Event()

    def one(job: tuple) -> None:
        nonlocal spent
        arm, item, text = job
        if stop.is_set():
            return
        record = call(client, key, systems[arm["id"]], text, arm["body"])
        record.update({"arm": arm["id"], "item_id": item})
        with lock:
            rows.append(record)
            spent += record.get("cost") or 0.0
            print(f"  {arm['id']:16} {item}  {record['status']:8} "
                  f"compl={record.get('completion_tokens')} "
                  f"reason={record.get('reasoning_tokens')} "
                  f"keys={record.get('keys')} ${spent:.3f}", flush=True)
            if spent >= args.cap_usd and not stop.is_set():
                stop.set()
                print(f"\nSTOPPING: spend cap ${args.cap_usd:.2f} reached (${spent:.2f}). "
                      f"Calls not yet started are skipped; any arm left with fewer "
                      f"successful calls than the others is reported with its own n and "
                      f"not silently pooled.", flush=True)

    with httpx.Client(timeout=900.0) as client:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            list(pool.map(one, work))

    print(f"\n{'arm':16} {'n':>3} {'median compl':>13} {'median reason':>14} "
          f"{'median keys':>12} {'json ok':>8} {'$ total':>9}")
    summary = []
    for arm in ARMS:
        got = [r for r in rows if r["arm"] == arm["id"] and r["status"] == "ok"]
        if not got:
            print(f"{arm['id']:16} {'0':>3}   no successful calls")
            continue
        compl = statistics.median(r["completion_tokens"] or 0 for r in got)
        reason = statistics.median(r["reasoning_tokens"] or 0 for r in got)
        keys = statistics.median(r["keys"] for r in got)
        cost = sum(r["cost"] or 0 for r in got)
        ok = sum(1 for r in got if r["json_ok"])
        print(f"{arm['id']:16} {len(got):>3} {compl:>13,.0f} {reason:>14,.0f} "
              f"{keys:>12,.0f} {ok:>4}/{len(got):<3} {cost:>9.3f}")
        summary.append({"arm": arm["id"], "n": len(got), "median_completion": compl,
                        "median_reasoning": reason, "median_keys": keys,
                        "json_ok": ok, "cost_usd": round(cost, 4)})

    print("\n  reasoning_tokens is a BREAKDOWN OF completion_tokens, not an addition to it.")
    print("  A backend that reports no breakdown yields 0 meaning 'not reported', which is")
    print("  not the same as 'none spent'. OpenRouter is not Vertex: these arms test the")
    print("  PROMPT and SCHEMA faithfully and the thinking budget only by proxy.")
    print("  No accuracy is measured here -- this repo has no sentiment_qa ground truth.")

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps({
        "model": MODEL, "items": len(calls), "cap_usd": args.cap_usd,
        "spent_usd": round(spent, 4), "summary": summary, "rows": rows,
    }, indent=2), encoding="utf-8")
    print(f"\n  wrote {args.json}  (spent ${spent:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
