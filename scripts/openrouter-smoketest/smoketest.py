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

# Windows consoles default to a legacy codepage (cp874 for Thai locales), which raises
# UnicodeEncodeError on any character outside it. That is fatal here for two reasons:
# a model may return an emoji, and more importantly the real evaluation data is THAI.
# A script that dies when a model speaks Thai is useless for this project.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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


# Both current defaults are REASONING models: they spend completion tokens on internal
# reasoning before emitting any content. Measured on a one-word prompt, gemini-3.6-flash
# burned 96 reasoning tokens and qwen3.7-flash burned 177, for a two-character answer.
#
# The first version of this script used max_tokens=10 and reported PASS for both while
# content came back empty and finish_reason was "length": the whole budget went to
# reasoning and nothing was left to answer with. A budget this large is not generosity,
# it is the minimum that lets a reasoning model finish thinking and then speak.
MAX_TOKENS = 2000


def check_model(client: OpenAI, model_id: str, label: str) -> bool:
    print(f"--- {label}: {model_id} ---")
    start = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "user", "content": "Reply with exactly one word: OK"}
            ],
            max_tokens=MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001 - report whatever OpenRouter/HTTP gives us
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        return False

    elapsed = time.monotonic() - start
    choice = response.choices[0]
    text = (choice.message.content or "").strip()
    observed_model = response.model  # what actually answered, not what we requested
    print(f"  responded in {elapsed:.2f}s")
    print(f"  requested model : {model_id}")
    print(f"  observed model  : {observed_model}")
    print(f"  finish_reason   : {choice.finish_reason}")
    print(f"  response        : {text!r}")

    reasoning_tokens = None
    if response.usage:
        details = getattr(response.usage, "completion_tokens_details", None)
        reasoning_tokens = getattr(details, "reasoning_tokens", None) if details else None
        line = (
            f"  tokens          : in={response.usage.prompt_tokens} "
            f"out={response.usage.completion_tokens}"
        )
        if reasoning_tokens:
            visible = response.usage.completion_tokens - reasoning_tokens
            line += f" (reasoning={reasoning_tokens}, visible={visible})"
        print(line)
        cost = getattr(response.usage, "cost", None)
        if cost is not None:
            print(f"  cost            : ${cost:.6f}")

    # PASS requires an actual answer, not merely an HTTP 200. A reasoning model that
    # spends its whole budget thinking returns finish_reason="length" with empty
    # content, which is a failure however successful the request looked.
    if not text:
        print("  FAILED: no content returned.")
        if choice.finish_reason == "length":
            print(
                f"          finish_reason is 'length' and reasoning used "
                f"{reasoning_tokens} tokens: the model never got to answer. "
                f"Raise MAX_TOKENS (currently {MAX_TOKENS})."
            )
        return False

    return True


def check_thai(client: OpenAI, model_id: str, label: str) -> tuple[bool, bool]:
    """Round-trip Thai text. The evaluation corpus is Thai call content, so a suite
    that only sends ASCII proves nothing about the workload that matters.

    Returns (thai_survived_the_pipe, reproduced_exactly).
    """
    thai = "ลูกค้าต้องการยกเลิกบริการเพราะสัญญาณไม่ดี"
    try:
        response = client.chat.completions.create(
            model=model_id,
            messages=[{
                "role": "user",
                "content": f"Repeat this Thai sentence back exactly, nothing else:\n{thai}",
            }],
            max_tokens=MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:12s} FAILED: {type(exc).__name__}: {exc}")
        return False, False

    text = (response.choices[0].message.content or "").strip()
    if not text:
        finish = response.choices[0].finish_reason
        print(f"  {label:12s} FAILED: empty response (finish_reason={finish})")
        return False, False

    has_thai = any("฀" <= ch <= "๿" for ch in text)

    # Exact means EQUAL, not "contains". Substring containment silently passes a reply
    # that duplicated a leading character, which is exactly what one model did here:
    # the original is a substring of the corrupted output, so `thai in text` was True
    # while the strings differed. For a transcription comparison that distinction is
    # the entire point.
    exact = text == thai

    if exact:
        status = "exact match"
    elif has_thai:
        status = f"Thai returned but NOT exact (got {len(text)} chars, expected {len(thai)})"
    else:
        status = "NO THAI CHARACTERS"

    print(f"  {label:12s} {model_id}")
    print(f"               {status}")
    print(f"               {text[:70]!r}")
    if has_thai and not exact:
        # Show the first divergence so the difference is inspectable rather than implied.
        for i, (a, b) in enumerate(zip(thai, text)):
            if a != b:
                print(f"               first difference at char {i}: expected {a!r}, got {b!r}")
                break

    # Two-part verdict on purpose. "Thai survived the pipe" is a plumbing question;
    # "Thai came back faithfully" is a quality one. Collapsing them into a single PASS
    # would hide a model returning fluent-looking but corrupted Thai, which is the
    # failure mode that matters most for a Thai call-centre migration.
    return has_thai, exact


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
        "incumbent": os.environ.get("OPENROUTER_MODEL_INCUMBENT", "google/gemini-2.5-flash"),
        "candidate": os.environ.get("OPENROUTER_MODEL_CANDIDATE", "qwen/qwen3.6-27b"),
        "candidate_2": os.environ.get("OPENROUTER_MODEL_CANDIDATE_2", "google/gemma-4-26b-a4b-it"),
    }

    results = {label: check_model(client, model_id, label) for label, model_id in models.items()}

    # Thai round-trip. The real evaluation data is Thai call content, so a suite that
    # only ever sends ASCII proves nothing about the workload that matters.
    print()
    print("=== Thai round-trip ===")
    thai_results = {}
    for label, model_id in models.items():
        thai_results[label] = check_thai(client, model_id, label)

    print()
    print("=== summary ===")
    for label, ok in results.items():
        print(f"  {label:14s} reachable   : {'PASS' if ok else 'FAIL'}")
    for label, (has_thai, exact) in thai_results.items():
        if has_thai and exact:
            verdict = "PASS (exact)"
        elif has_thai:
            verdict = "PASS pipe / **CORRUPTED** text -- returned Thai, but not what was sent"
        else:
            verdict = "FAIL (no Thai returned)"
        print(f"  {label:14s} thai        : {verdict}")

    if any(has_thai and not exact for has_thai, exact in thai_results.values()):
        print()
        print("NOTE: a model returned Thai that does not match what was sent. Single")
        print("      sample, default temperature, so this is a signal to investigate,")
        print("      not a benchmark result. But for a Thai call-centre migration it is")
        print("      exactly the failure mode worth measuring properly.")

    reachable_ok = all(results.values())
    thai_ok = all(has_thai for has_thai, _ in thai_results.values())
    return 0 if (reachable_ok and thai_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
