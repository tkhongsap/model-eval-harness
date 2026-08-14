"""Ask each Token Factory chat model the same software-engineering questions, and record
what came back: the prompt, the reply, both token counts and the latency.

A shallow capability sample, not an evaluation. There is no ground truth here and no
scoring -- it answers "does this endpoint produce sensible engineering answers, and how
fast", which is the question you ask before deciding whether an evaluation is worth
running. Nothing about it is a quality verdict, and it does not touch `src/evalharness/`.

Decoding is pinned so the numbers mean something: `temperature=0`, and **`top_p` is
omitted entirely**. MEASURED 2026-08-14: every model on this endpoint rejects `top_p = 0`
with `400 top_p must be in (0, 1]`, raised by the vLLM server beneath the gateway -- even
though the published `openapi.yaml` declares `minimum: 0`. Omitting the field is the only
option that is both accepted and unambiguous; sending `0.0000001` would work but would be
a number nobody chose.

The key is read from `TOKEN_FACTORY_API_KEY` (environment, then `.env`) and is never
printed. Writes a JSON record next to the run for the HTML report to consume.

Usage:
    python scripts/token_factory_bench.py
    python scripts/token_factory_bench.py --model gemma-4-12b-it --out bench.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
KEY_VAR = "TOKEN_FACTORY_API_KEY"
HOSTNAME = "token-fac-api.truecorp.co.th"
INTERNAL_IP = "10.94.154.102"

# Four questions, deliberately different in shape: a definition, an explanation with an
# example, a structured trade-off list, and code generation. Together they show how each
# model's output length and latency move with the kind of question, which a single prompt
# cannot.
QUESTIONS = [
    (
        "definition",
        "What is the difference between a mutex and a semaphore? Answer in under 80 words.",
    ),
    (
        "explanation",
        "Explain what a race condition is, and give one concrete example from a web "
        "application.",
    ),
    (
        "trade-offs",
        "When should a team use a message queue instead of a direct synchronous API call? "
        "List three trade-offs.",
    ),
    (
        "code",
        "Write a Python function that reverses a singly linked list. Include a short "
        "docstring. Code only.",
    ),
]


def _load_key() -> str | None:
    value = os.environ.get(KEY_VAR, "").strip()
    if value:
        return value
    env_file = REPO_ROOT / ".env"
    if not env_file.is_file():
        return None
    match = re.search(
        rf"^{re.escape(KEY_VAR)}=(.*)$", env_file.read_text(encoding="utf-8"), re.M
    )
    return (match.group(1).strip() or None) if match else None


def _client(key: str, timeout: float):
    import httpx
    from openai import OpenAI

    warnings.filterwarnings("ignore", message="Unverified HTTPS request")
    return OpenAI(
        api_key=key,
        base_url=f"https://{INTERNAL_IP}/v1",
        http_client=httpx.Client(
            verify=False, timeout=timeout, headers={"Host": HOSTNAME}
        ),
        max_retries=0,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="token_factory_bench")
    parser.add_argument("--model", action="append", default=None)
    parser.add_argument("--max-tokens", type=int, default=400)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--out", default="token_factory_bench.json")
    args = parser.parse_args(argv)

    key = _load_key()
    if not key:
        print(f"{KEY_VAR} not found in the environment or .env")
        return 2
    client = _client(key, args.timeout)

    served = [model.id for model in client.models.list().data]
    print(f"served: {', '.join(served)}")

    # The ASR model is not a chat model -- it answered `language None<asr_text>` to a plain
    # text prompt. Asking it engineering questions would produce noise and present it as a
    # failure, when it is simply a different kind of model. Excluded by default, and the
    # exclusion is recorded rather than silent.
    excluded = {name: "speech-recognition model, not a chat model" for name in served
                if "asr" in name.lower()}
    targets = args.model or [name for name in served if name not in excluded]

    records = []
    for model_name in targets:
        for kind, prompt in QUESTIONS:
            print(f"  {model_name:20} {kind:12} ...", end="", flush=True)
            started = time.monotonic()
            row = {"model": model_name, "kind": kind, "prompt": prompt}
            try:
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=args.max_tokens,
                    temperature=0.0,  # top_p omitted on purpose -- see the module docstring
                )
            except Exception as exc:  # noqa: BLE001
                row.update(
                    error=f"{type(exc).__name__}: {str(exc)[:300]}",
                    latency_s=round(time.monotonic() - started, 3),
                )
                records.append(row)
                print(" FAILED")
                continue

            latency = time.monotonic() - started
            choice = response.choices[0] if response.choices else None
            usage = getattr(response, "usage", None)
            text = (getattr(choice.message, "content", None) if choice else None) or ""
            completion_tokens = getattr(usage, "completion_tokens", None)
            row.update(
                reply=text.strip(),
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=completion_tokens,
                reply_chars=len(text.strip()),
                finish_reason=getattr(choice, "finish_reason", None),
                observed_model=getattr(response, "model", None),
                latency_s=round(latency, 3),
                tokens_per_s=(
                    round(completion_tokens / latency, 1)
                    if completion_tokens and latency > 0
                    else None
                ),
            )
            records.append(row)
            print(f" {latency:.2f}s, {completion_tokens} tok")

    payload = {
        "captured_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "endpoint": f"https://{HOSTNAME}/v1 (via {INTERNAL_IP})",
        "served_models": served,
        "excluded": excluded,
        "decoding": {"temperature": 0.0, "top_p": "omitted (0 is rejected)",
                     "max_tokens": args.max_tokens},
        "records": records,
    }
    out = Path(args.out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {out}  ({len(records)} calls)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
