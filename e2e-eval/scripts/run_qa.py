"""Run one end-to-end arm over the pilot pack and record its QA JSON per item.

FIVE ARMS, ONE CODE PATH. Every arm -- audio or text, Gemini or open -- goes through the
same request builder, the same `outcomes.classify`, the same `flatten.to_rows` and the same
`metrics.score_*`. That is the only way the numbers subtract from each other. An arm with
its own parse rule or its own grain produces a number on a different scale and the delta
between them means nothing, which is the failure `outcomes.py` already exists to prevent.

    arm              transcript source           model                     modality
    ---------------- --------------------------- ------------------------- --------
    gemini-audio     (none -- the wav itself)    google/gemini-2.5-flash   audio
    gemini-text      pack.transcript_th          google/gemini-2.5-flash   text
    qwen-text        pack.transcript_th          qwen/qwen3.6-27b          text
    qwenasr-qwen     ASR output, qwen3-asr-1.7b  qwen/qwen3.6-27b          text
    whisper-qwen     ASR output, whisper-large-v3 qwen/qwen3.6-27b         text

`gemini-audio` is the incumbent and is the ONLY arm that matches what production does
today: one call, wav in, QA JSON out. The other four exist to decompose the gap.

WHY THE AUDIO ARM STILL GETS THE RETENTION PROMPT. Production's voice app uses its own
much larger KPI prompt, which this repository has no scorer for. Holding the prompt fixed
across all five arms and varying only the input modality is what makes the comparison a
comparison. The cost is that `gemini-audio` is not byte-identical to production's voice
call; that is recorded in the report rather than silently traded away.

NO RETRY ON A PARSE FAILURE, deliberately, matching `runner.py`'s refusals: a harness that
re-rolls until the JSON is valid reports coverage it did not earn.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(REPO / "src"))

from evalgen.decoding import decoding_schema          # noqa: E402
from evalgen.outcomes import classify                 # noqa: E402
from evalgen.prompts import get as get_prompt         # noqa: E402
from evalgen.runner import _required_keys             # noqa: E402

PACK = ROOT / "pilot.jsonl"
HYP = ROOT / "hypotheses"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

ARMS: dict[str, dict] = {
    "gemini-audio":  dict(model="google/gemini-2.5-flash", modality="audio", source=None),
    "gemini-text":   dict(model="google/gemini-2.5-flash", modality="text",  source="reference"),
    "qwen-text":     dict(model="qwen/qwen3.6-27b",        modality="text",  source="reference"),
    "qwenasr-qwen":  dict(model="qwen/qwen3.6-27b",        modality="text",
                          source="asr:e2e-qwen3-asr-1.7b"),
    "whisper-qwen":  dict(model="qwen/qwen3.6-27b",        modality="text",
                          source="asr:e2e-whisper-large-v3"),
}


def api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        return key
    env = REPO / ".env"
    if not env.exists():
        env = pathlib.Path(r"C:/Users/te90056471/my-github/model-eval-harness/.env")
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("OPENROUTER_API_KEY not available")


def transcript_for(rec: dict, source: str) -> str:
    if source == "reference":
        return rec["transcript_th"]
    if source.startswith("asr:"):
        arm = source.split(":", 1)[1]
        p = ROOT / "asr" / arm / f"{rec['item_id']}.txt"
        if not p.exists():
            raise FileNotFoundError(f"missing ASR transcript {p}")
        return p.read_text(encoding="utf-8").strip()
    raise ValueError(source)


def response_format() -> dict:
    """Exactly what `cli._response_format` builds, including the three declared deviations.

    Rebuilt here rather than imported because that helper is a private function taking a
    path; the schema file and `decoding_schema` are the real contract and both are used
    directly. If `DEVIATIONS` ever moves, `decoding_schema` raises rather than silently
    applying half of itself.
    """
    raw = json.loads((REPO / "src" / "evalgen" / "schemas" / "retention.json")
                     .read_text(encoding="utf-8"))
    schema = decoding_schema(raw)
    return {"type": "json_schema",
            "json_schema": {"name": schema.get("title", "analyze_call"), "schema": schema}}


def build_body(rec: dict, arm: dict, schema: dict) -> dict:
    system = get_prompt("v9_16_base").system_text
    if arm["modality"] == "audio":
        wav = (ROOT / "audio" / rec["audio_file"]).read_bytes()
        user = [{
            "type": "input_audio",
            "input_audio": {"data": base64.b64encode(wav).decode("ascii"), "format": "wav"},
        }]
    else:
        user = transcript_for(rec, arm["source"])
    return {
        "model": arm["model"],
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "response_format": schema,
        # The flag that actually guarantees the schema was enforced: OpenRouter refuses an
        # endpoint that cannot honour `response_format` instead of dropping the parameter
        # and serving prompt-coaxed output that looks identical (cli.py:342-355).
        "provider": {"require_parameters": True},
        "temperature": 0.0,
        # 4096 truncated Qwen mid-object on the very first item: the `recommendation` field
        # is free Thai prose, and Thai costs far more tokens per character than English, so
        # a budget sized by eye off the English schema cuts the JSON in half. A truncated
        # object classifies as `schema_violation`, which is indistinguishable in the report
        # from a model that cannot follow the schema -- it would have been recorded as a
        # Qwen capability failure when it was the harness's budget.
        "max_tokens": 16384,
    }


def post(body: dict, key: str, timeout: int = 600,
         attempts: int = 4) -> tuple[str | None, str | None, dict]:
    """POST once, retrying ONLY failures that produced no generation.

    The distinction is the whole point and it mirrors `runner.py`'s `--max-attempts`:

      * A response that arrived and is malformed is THE MEASUREMENT. It is never retried,
        because a harness that re-rolls until the JSON parses reports coverage it did not
        earn, and the arm that needed three attempts scores like the arm that needed one.
      * A call that never delivered a response is NOT a measurement of anything. The first
        run of this pipeline lost 45 items to `IncompleteRead` and Windows `getaddrinfo`
        failures after 5 arms x 6 workers opened 30 concurrent TLS connections; scoring
        those as failures would have recorded a DNS collapse as a Qwen quality result.

    So: retry transport, never retry content.
    """
    data = json.dumps(body).encode("utf-8")
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(
                ENDPOINT, data=data, method="POST",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            choice = (payload.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            return msg.get("content"), choice.get("finish_reason"), payload.get("usage") or {}
        except urllib.error.HTTPError:
            raise                       # a real answer from the server; the caller judges it
        except Exception as exc:        # noqa: BLE001 — transport only
            last = exc
            time.sleep(3.0 * (attempt + 1))
    raise RuntimeError(f"transport failed after {attempts} attempts: {last}") from last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("arm", choices=sorted(ARMS))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--resume", action="store_true",
                    help="only run items that have no recorded verdict yet")
    args = ap.parse_args()

    arm = ARMS[args.arm]
    key = api_key()
    schema = response_format()
    required = _required_keys(schema)
    out = HYP / args.arm
    out.mkdir(parents=True, exist_ok=True)

    recs = [json.loads(l) for l in PACK.read_text(encoding="utf-8").splitlines()]
    if args.limit:
        recs = recs[: args.limit]
    if args.resume:
        # Skip items that already reached a VERDICT -- parse_ok true or false. A recorded
        # `schema_violation` is a real measurement and re-running it would be exactly the
        # re-roll this harness refuses; only items with no verdict at all (transport) are
        # retried, and the counts below say how many.
        def has_verdict(rec: dict) -> bool:
            p = out / f"{rec['item_id']}.json"
            if not p.exists():
                return False
            try:
                return "parse_ok" in json.loads(p.read_text(encoding="utf-8"))
            except Exception:                                      # noqa: BLE001
                return False
        before = len(recs)
        recs = [r for r in recs if not has_verdict(r)]
        print(f"resume: {before - len(recs)} already have a verdict, {len(recs)} to run")
        if not recs:
            print("nothing to do")
            return 0

    # One item per worker. Qwen3.6-27B takes ~90 s per call here, so 30 items sequentially
    # is 45 minutes an arm; the calls are independent, so this is pure wall-clock waste.
    # Results are written into a slot per item and the log is re-sorted into testset order
    # afterwards, because `runner.py`'s refusal to reorder items applies just as much when
    # the reordering comes from a thread pool as when it comes from a sort.
    def one(idx: int, rec: dict) -> dict:
        t0 = time.time()
        try:
            body = build_body(rec, arm, schema)
            content, finish, usage = post(body, key)
            res = classify(content, finish, required_keys=required)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            return {"idx": idx, "item_id": rec["item_id"], "status": "transport",
                    "error": f"HTTP {exc.code}: {detail}"}
        except Exception as exc:                                   # noqa: BLE001
            return {"idx": idx, "item_id": rec["item_id"], "status": "transport",
                    "error": str(exc)}

        record = {"item_id": rec["item_id"], "parse_ok": res.parse_ok,
                  "outcome": str(getattr(res, "outcome", None)),
                  "finish_reason": finish, "payload": res.payload}
        if not res.parse_ok:
            # Keep the raw text of every failure. Without it a `schema_violation` is a
            # dead end -- truncation, a refusal and genuine schema disobedience all land in
            # the same bucket, and only the raw content tells them apart.
            record["raw_content"] = content
            record["usage"] = usage
        (out / f"{rec['item_id']}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"idx": idx, "item_id": rec["item_id"], "status": "ok",
                "parse_ok": res.parse_ok, "outcome": str(getattr(res, "outcome", "")),
                "seconds": round(time.time() - t0, 1),
                "usage": {k: usage.get(k) for k in
                          ("prompt_tokens", "completion_tokens", "total_tokens")}}

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(one, i, r): i for i, r in enumerate(recs)}
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            if r["status"] == "transport":
                print(f"  {r['item_id']:8s} TRANSPORT {r['error'][:80]}", flush=True)
            else:
                print(f"  {r['item_id']:8s} parse_ok={r['parse_ok']!s:5s} "
                      f"{r['seconds']:6.1f}s  {r['outcome']}", flush=True)

    log = [dict(r) for r in sorted(results, key=lambda r: r["idx"])]
    for r in log:
        r.pop("idx", None)
    ok = sum(1 for r in log if r.get("parse_ok"))
    bad = len(log) - ok

    # On --resume the log covers only the items just run, so merge it over any previous
    # log rather than replacing it -- otherwise the run record claims the arm is 10 items
    # long and the coverage number silently shrinks to match.
    run_path = out / "_run.json"
    merged = {r["item_id"]: r for r in log}
    if args.resume and run_path.exists():
        try:
            prev = json.loads(run_path.read_text(encoding="utf-8")).get("items", [])
            merged = {**{r["item_id"]: r for r in prev}, **merged}
        except Exception:                                          # noqa: BLE001
            pass
    all_items = list(merged.values())
    ok = sum(1 for r in all_items if r.get("parse_ok"))
    transport = sum(1 for r in all_items if r.get("status") == "transport")
    bad = len(all_items) - ok

    run_path.write_text(json.dumps(
        {"arm": args.arm, **{k: v for k, v in arm.items()},
         "prompt_id": "v9_16_base", "items": all_items,
         "parse_ok": ok, "not_ok": bad, "transport_failures": transport},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{args.arm}: {ok} parse_ok, {bad} not ({transport} transport) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
