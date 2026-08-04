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

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent

# Searched in order. The first file to define a variable wins, so a script-local .env
# overrides the repo-root one rather than the other way round. Both are gitignored by
# the bare `.env` rule in .gitignore, which matches at any depth.
ENV_FILES = (SCRIPT_DIR / ".env", REPO_ROOT / ".env")

# Accepted spellings for the key, in priority order. More than one exists because the
# obvious name is not the only obvious name: a key stored as OPEN_ROUTER_API is a
# perfectly reasonable guess, and failing with "not set" while the key sits in the
# file is a worse outcome than accepting a synonym.
API_KEY_VARS = ("OPENROUTER_API_KEY", "OPEN_ROUTER_API", "OPENROUTER_KEY")


def find_api_key() -> tuple[str, str] | tuple[None, None]:
    """Return (value, variable_name_it_came_from), or (None, None)."""
    for name in API_KEY_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value, name
    return None, None


def load_env_file(path: Path) -> bool:
    """Minimal .env loader so this script has no dependency beyond `openai`.

    Returns True if the file existed and was read.
    """
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        # setdefault: a real environment variable beats any file, and the first file
        # in ENV_FILES beats later ones.
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return True


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
    loaded = [p for p in ENV_FILES if load_env_file(p)]
    if loaded:
        for p in loaded:
            print(f"loaded env from: {p}")
    else:
        print("no .env file found (checked script dir and repo root)")

    api_key, key_var = find_api_key()
    if not api_key:
        searched = "\n".join(f"  - {p}" for p in ENV_FILES)
        names = ", ".join(API_KEY_VARS)
        print(
            f"No OpenRouter key found. Looked for any of: {names}\n"
            f"In these files:\n{searched}\n"
            "Add one to a .env in either location, or export it in your shell. "
            "See .env.example at the repo root.",
            file=sys.stderr,
        )
        return 1
    print(f"key loaded from {key_var}: ...{api_key[-4:]} ({len(api_key)} chars)")
    print()

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
