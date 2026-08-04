"""Smoke test: does the OpenRouter API key work, via the OpenAI SDK?

This is exploratory tooling, not part of the scoring library. It exists to answer one
question -- can we reach a model through OpenRouter with this key -- before any real
candidate-generation work happens. It makes no claim about model quality and produces
no scored output.

Deliberately outside src/evalharness/: that package's own docs state it makes no model
calls, and this script is the reason that boundary is written down rather than implied.

Usage:
    cp .env.example .env          # then fill in OPENROUTER_API_KEY
    pip install -r requirements.txt
    python smoketest.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    print("Missing dependency. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

ENV_FILE = Path(__file__).parent / ".env"


def load_env_file(path: Path) -> None:
    """Minimal .env loader so this script has no dependency beyond `openai`."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def check_model(client: OpenAI, model_id: str, label: str) -> bool:
    print(f"--- {label}: {model_id} ---")
    start = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "user", "content": "Reply with exactly one word: OK"}
            ],
            max_tokens=10,
        )
    except Exception as exc:  # noqa: BLE001 - report whatever OpenRouter/HTTP gives us
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        return False

    elapsed = time.monotonic() - start
    text = (response.choices[0].message.content or "").strip()
    observed_model = response.model  # what actually answered, not what we requested
    print(f"  OK in {elapsed:.2f}s")
    print(f"  requested model : {model_id}")
    print(f"  observed model  : {observed_model}")
    print(f"  response        : {text!r}")
    if response.usage:
        print(f"  tokens          : in={response.usage.prompt_tokens} out={response.usage.completion_tokens}")
    return True


def main() -> int:
    load_env_file(ENV_FILE)

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        print(
            "OPENROUTER_API_KEY is not set. Copy .env.example to .env and fill it in.",
            file=sys.stderr,
        )
        return 1

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        default_headers={
            # Optional, OpenRouter uses these for its public leaderboard. Harmless to omit.
            "HTTP-Referer": "https://github.com/tkhongsap/model-eval-harness",
            "X-Title": "model-eval-harness smoketest",
        },
    )

    models = {
        "incumbent": os.environ.get("OPENROUTER_MODEL_INCUMBENT", "google/gemini-3.6-flash"),
        "candidate": os.environ.get("OPENROUTER_MODEL_CANDIDATE", "qwen/qwen3.7-flash"),
    }

    results = {label: check_model(client, model_id, label) for label, model_id in models.items()}
    print()
    print("=== summary ===")
    for label, ok in results.items():
        print(f"  {label}: {'PASS' if ok else 'FAIL'}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
