"""Run independent MODEL reviewers over the blind audit packet.

    PYTHONPATH=src python scripts/audit_review_models.py --jobs 8
    PYTHONPATH=src python scripts/audit_review_models.py --dry-run

WHAT THIS IS, AND WHAT IT IS NOT. The audit was designed for human reviewers, and a human
panel remains the thing that would settle it. This is the weaker substitute that is actually
available: three frontier models, each shown ONLY the transcript and the written spec, each
asked for the label it would give. It is a real independent re-labelling in the mechanical
sense -- no reviewer sees the corpus's answer, or any other model's -- and it is NOT a human
audit. Every report built on it says so.

WHY IT IS WORTH RUNNING ANYWAY. The question is not subtle. "Which service is this call
about" and "did the customer leave" are answerable from a transcript by any competent reader,
and the specific dispute under test -- 26 calls where a labeller reading a PERFECT transcript
named a different product than the corpus -- is exactly the kind of disagreement where a third
opinion is informative. If three unrelated models independently land on the corpus's label,
the corpus is probably right. If they independently land somewhere else, the corpus is
probably wrong, and that is worth knowing before a human panel is convened rather than after.

INDEPENDENCE IS THE WHOLE DESIGN, so the reviewers are chosen against the eval:

    under test          reviewer            why it is independent
    Gemini 2.5 Flash    Claude Sonnet 4.5   different lab, different family
    Qwen3.8-27B         GPT-4.1             different lab, different family
    Qwen3-ASR           Llama 3.3 70B       different lab, different family
    Typhoon Whisper

Anthropic, OpenAI and Meta. No Google model reviews a Google arm; no Qwen model reviews
the Qwen labeller. Using
`gemini-2.5-pro` would have been cheaper and would have been self-review.

THREE, NOT TWO, AND THAT IS ARITHMETIC RATHER THAN BUDGET. With two reviewers a strict
majority IS unanimity, so every disagreement becomes an unresolved tie -- measured at 32 of 68
cases in a smoke test. Three reviewers give a majority that can actually resolve.

BLINDNESS IS STRUCTURAL HERE. This script reads the answer key ONLY to map a case id to the
item whose transcript it must fetch. `build_prompt` is a pure function of (transcript, spec)
and cannot see a label -- there is no parameter through which one could arrive. The test file
asserts that.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import csv
import importlib.util
import json
import os
import re
import sys
import threading
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from evalharness.labelspaces import RETENTION  # noqa: E402

OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
PRODUCTS = ("Postpaid", "TOL", "TVS", "unknown")

# Chosen against the eval -- see the module docstring. `id` is also the output filename, so it
# is what shows up as a "reviewer" in audit_score.py.
REVIEWERS = (
    {"id": "claude-sonnet-4.5", "model": "anthropic/claude-sonnet-4.5"},
    {"id": "gpt-4.1", "model": "openai/gpt-4.1"},
    # Meta rather than Mistral: mistral-large returned HTTP 429 "temporarily rate-limited
    # upstream" on every probe call, and a reviewer that cannot answer is not a reviewer.
    {"id": "llama-3.3-70b", "model": "meta-llama/llama-3.3-70b-instruct"},
)

SYSTEM = """You are auditing the ground truth of a Thai call-centre evaluation set.

You will be given ONE call transcript and the written labelling rules. Apply the rules to the
transcript and report the label you would give.

This is an audit. You are not being asked to guess what someone else wrote, and you are not
being shown anyone else's answer. If the rules and your intuition disagree, follow the RULES
and say so. If the call genuinely fits no label, say that in your reason rather than forcing
the nearest one -- a call the rules cannot label is a finding about the rules.

Reply with ONLY a JSON object, no other text:

{"product": "<Postpaid|TOL|TVS|unknown>",
 "call_result": "<save|churn|unknown|undefined>",
 "evidence": "<the exact line from the transcript that decided it>",
 "reason": "<one sentence: which rule you applied>"}"""


class Refused(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"audit_review_models REFUSING: {message}")


def load_env() -> None:
    path = REPO / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def packet_module():
    spec = importlib.util.spec_from_file_location(
        "audit_packet", REPO / "scripts" / "audit_packet.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_prompt(turns: list[tuple[str, str]], spec: str) -> str:
    """The reviewer's whole view. A pure function of the transcript and the rules.

    There is deliberately no `label`, `expected` or `group` parameter. Blindness here is not
    a matter of remembering to omit something -- there is nothing to omit.
    """
    body = "\n".join(f"{speaker}: {text}" for speaker, text in turns)
    return (f"THE WRITTEN RULES\n{'=' * 60}\n{spec}\n\n"
            f"THE CALL\n{'=' * 60}\n{body}\n\n"
            "Apply the rules above to this call and reply with the JSON object only.")


def parse(content: str) -> dict | None:
    """Accept a bare object or one wrapped in prose/fences; reject anything off-vocabulary."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except Exception:
        return None
    product = str(data.get("product", "")).strip()
    outcome = str(data.get("call_result", "")).strip()
    if product not in PRODUCTS or outcome not in RETENTION.call_result:
        return None
    return {"product": product, "call_result": outcome,
            "evidence": str(data.get("evidence", ""))[:300],
            "reason": str(data.get("reason", ""))[:300]}


def call(client: httpx.Client, key: str, model: str, prompt: str) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 700,
        "usage": {"include": True},
    }
    # Three attempts, and only for 429 / transport. A retried 200 would be a second draw
    # from the same reviewer on the same case, and keeping the luckier one is exactly the
    # bias this audit exists to detect.
    import time
    response = None
    for attempt in range(3):
        try:
            response = client.post(
                OPENROUTER, json=payload,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"})
        except Exception as exc:                                      # noqa: BLE001
            if attempt == 2:
                return {"status": f"transport: {type(exc).__name__}"}
            time.sleep(2.0 * (attempt + 1))
            continue
        if response.status_code == 429 and attempt < 2:
            time.sleep(4.0 * (attempt + 1))
            continue
        break
    if response is None or response.status_code != 200:
        return {"status": f"http_{response.status_code if response else 0}",
                "detail": (response.text[:200] if response else "")}
    data = response.json()
    content = data["choices"][0]["message"].get("content") or ""
    parsed = parse(content)
    usage = data.get("usage") or {}
    if parsed is None:
        return {"status": "unparseable", "raw": content[:200],
                "cost": usage.get("cost")}
    parsed.update(status="ok", cost=usage.get("cost"),
                  observed_model=data.get("model"), provider=data.get("provider"))
    return parsed


def main() -> int:
    ap = argparse.ArgumentParser(prog="audit_review_models")
    ap.add_argument("--key", type=Path, default=REPO / "out" / "audit-answer-key.csv")
    ap.add_argument("--pack", type=Path, default=REPO / "asr-eval-v2")
    ap.add_argument("--out-dir", type=Path, default=REPO / "out" / "audit-reviews")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="first N cases, for a smoke run")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    load_env()
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key and not args.dry_run:
        raise Refused("no OPENROUTER_API_KEY in environment or .env")

    packet = packet_module()
    spec = packet.spec_text()

    if not args.key.exists():
        raise Refused(f"no answer key at {args.key}; run audit_packet.py first")
    cases = []
    with args.key.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            # ONLY case_id and item_id are read. The expected_* columns are never touched.
            cases.append({"case_id": row["case_id"], "item_id": row["item_id"]})
    if args.limit:
        cases = cases[:args.limit]

    for case in cases:
        case["turns"] = packet.transcript_turns(args.pack, case["item_id"])

    print(f"blind model audit -- {len(cases)} cases x {len(REVIEWERS)} reviewers "
          f"= {len(cases) * len(REVIEWERS)} calls")
    for reviewer in REVIEWERS:
        print(f"  {reviewer['id']:20} {reviewer['model']}")
    print(f"  spec {len(spec):,} chars; blind -- no expected label reaches any prompt")
    if args.dry_run:
        print("\ndry run: no calls made")
        return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    spend = {"usd": 0.0}
    results: dict[str, dict[str, dict]] = {r["id"]: {} for r in REVIEWERS}
    work = [(r, c) for r in REVIEWERS for c in cases]

    with httpx.Client(timeout=300.0) as client:
        def one(job):
            reviewer, case = job
            out = call(client, api_key, reviewer["model"],
                       build_prompt(case["turns"], spec))
            with lock:
                results[reviewer["id"]][case["case_id"]] = out
                spend["usd"] += out.get("cost") or 0.0
                done = sum(len(v) for v in results.values())
                if done % 20 == 0 or out.get("status") != "ok":
                    print(f"  [{done}/{len(work)}] {reviewer['id']:18} "
                          f"{out.get('status')}  ${spend['usd']:.3f}", flush=True)

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            list(pool.map(one, work))

    print()
    for reviewer in REVIEWERS:
        rows = results[reviewer["id"]]
        statuses = collections.Counter(r.get("status") for r in rows.values())
        ok = statuses.get("ok", 0)
        path = args.out_dir / f"{reviewer['id']}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, ["case_id", "product", "call_result", "evidence"])
            writer.writeheader()
            for case_id, row in sorted(rows.items()):
                if row.get("status") != "ok":
                    continue
                writer.writerow({"case_id": case_id, "product": row["product"],
                                 "call_result": row["call_result"],
                                 "evidence": (row.get("evidence") or "").replace("\n", " ")})
        observed = {r.get("observed_model") for r in rows.values() if r.get("observed_model")}
        print(f"  {reviewer['id']:20} {ok}/{len(rows)} ok  {dict(statuses)}")
        print(f"  {'':20} served by {observed or '<not reported>'}")
        print(f"  {'':20} -> {path}")

    (args.out_dir / "_raw.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  spent ${spend['usd']:.3f}")
    print(f"  raw   {args.out_dir / '_raw.json'}")
    print("\n  These are MODEL reviewers, not humans. Weaker evidence than a human panel,")
    print("  and every report built on them has to say so.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
