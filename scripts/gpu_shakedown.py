"""Run the GPU shakedown: can Token Factory serve a decision-grade arm, and what will it cost?

The plan, the goal and the declared deviation are in `docs/gpu-shakedown-plan.md`. Read that
first; this file executes it and does not restate it.

**Every measurement goes through `scripts/evalgen.py`.** Nothing here re-implements a call
loop, a scorer or a stability metric. A shakedown that probed a different request shape than
a run sends would qualify a call nobody makes -- the argument `scripts/provider_probe.py`
already makes -- so this is an orchestrator, and the harness under test is the harness.

What it establishes, in the order a failure would kill the full run:

  Q3  determinism at temperature 0   -- `evalgen stability`, N_flip over 5 replicates
  Q4  one arm is one backend         -- the prompt_tokens fingerprint, same command
  Q5  wall-clock and concurrency     -- the same probe timed at 1, 4 and 8
  --  a first quality reading        -- `evalgen baseline` on retention_v1, then `compare`

Q1 (verified TLS) and Q2 (the real request shape, including the 531-line response schema)
were settled before this script existed and are recorded in the plan.

Not tested: `qwen3-asr-1.7b`. It is a speech model and answers text prompts with
`language None<asr_text>`. Testing it needs audio fixtures this repository does not have.
Recorded as untested rather than counted as a failure.

Usage:
    python scripts/gpu_shakedown.py                  # full run, roughly 10-15 minutes
    python scripts/gpu_shakedown.py --quick          # stability only, roughly 3 minutes
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVALGEN = REPO_ROOT / "scripts" / "evalgen.py"
MANIFEST = REPO_ROOT / "configs" / "runtime.token-factory.json"
CERT = REPO_ROOT / "configs" / "token-factory.crt.pem"
RUNS = REPO_ROOT / "out" / "runs"

CHAT_MODELS = ("gemma-4-12b-it", "qwen3.6-27b-fp8")
STABILITY_ITEMS = "RET-01,RET-11,RET-16"  # the trio the pin-proof runbook already uses

# Declared in the plan: `top_p = 0` is rejected by this endpoint, and with temperature 0 the
# decode is greedy so top_p is inert. 1.0 is the neutral value the CLI can already express.
TOP_P = "1.0"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    # The endpoint is self-signed; trust exactly its pinned certificate and nothing else.
    env["SSL_CERT_FILE"] = str(CERT)
    env.setdefault("EVAL_HARNESS_KEY_HMAC", "gpu-shakedown")
    return env


def run(label: str, args: list[str], *, timeout: float = 1800) -> dict:
    """One evalgen invocation, timed, with its exit code kept rather than swallowed."""
    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
    before = set(RUNS.iterdir()) if RUNS.exists() else set()
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(EVALGEN), *args],
        cwd=REPO_ROOT, env=_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    elapsed = time.monotonic() - started
    created = sorted(set(RUNS.iterdir()) - before) if RUNS.exists() else []
    tail = "\n".join((proc.stdout or "").strip().splitlines()[-14:])
    print(tail)
    if proc.returncode != 0:
        print(f"  exit={proc.returncode}")
        err = (proc.stderr or "").strip().splitlines()[-6:]
        if err:
            print("  " + "\n  ".join(err))
    print(f"  [{elapsed:.1f}s]")
    return {
        "label": label, "args": args, "exit_code": proc.returncode,
        "elapsed_s": round(elapsed, 2),
        "run_dir": str(created[-1]) if created else None,
        "stdout": proc.stdout, "stderr": proc.stderr,
    }


def _grep(text: str, needle: str) -> str | None:
    for line in (text or "").splitlines():
        if needle in line:
            return line.strip()
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gpu_shakedown")
    parser.add_argument("--quick", action="store_true", help="stability only")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", default="gpu_shakedown_results.json")
    args = parser.parse_args(argv)

    if not CERT.is_file() or not MANIFEST.is_file():
        print(f"missing {CERT} or {MANIFEST}")
        return 2

    common = [
        "--runtime-manifest", str(MANIFEST),
        "--top-p", TOP_P,
        "--out", str(RUNS),
    ]
    results: list[dict] = []
    started_all = time.monotonic()

    # --- Q3 + Q4: determinism, and one arm is one backend --------------------------
    stability_runs: dict[str, str | None] = {}
    for model in CHAT_MODELS:
        r = run(
            f"Q3/Q4  stability  {model}  ({STABILITY_ITEMS}, {args.repeats} replicates)",
            ["stability", "--arm", f"tf-{model}", "--items", STABILITY_ITEMS,
             "--repeats", str(args.repeats), "--model", model,
             "--concurrency", str(args.concurrency), *common],
        )
        results.append(r)
        stability_runs[model] = r["run_dir"]

    # --- Q5: does throughput hold as concurrency rises? ----------------------------
    if not args.quick:
        for level in (1, 8):
            model = CHAT_MODELS[0]
            results.append(run(
                f"Q5     concurrency {level}  {model}",
                ["stability", "--arm", f"tf-c{level}", "--items", STABILITY_ITEMS,
                 "--repeats", "2", "--model", model,
                 "--concurrency", str(level), *common],
            ))

    # --- a first quality reading, published beside its sample size -----------------
    baseline_runs: dict[str, str | None] = {}
    if not args.quick:
        for model in CHAT_MODELS:
            r = run(
                f"quality  baseline  {model}  (retention_v1, 20 items x 2)",
                ["baseline", "--arm", f"tf-base-{model}", "--model", model,
                 "--repeats", "2", "--concurrency", str(args.concurrency), *common],
            )
            results.append(r)
            baseline_runs[model] = r["run_dir"]

        a, b = (baseline_runs.get(m) for m in CHAT_MODELS)
        if a and b:
            results.append(run(
                "compare  gemma-4-12b-it vs qwen3.6-27b-fp8",
                ["compare", "--incumbent", a, "--candidate", b,
                 "--prompts-may-differ"],
            ))

    total = time.monotonic() - started_all

    # --- summary -------------------------------------------------------------------
    print(f"\n{'=' * 78}\nSUMMARY\n{'=' * 78}")
    for model in CHAT_MODELS:
        out = next((r["stdout"] for r in results
                    if r["label"].startswith("Q3/Q4") and model in r["label"]), "")
        print(f"\n{model}")
        for needle in ("N_flip =", "prompt_tokens fingerprint", "outcome counts"):
            line = _grep(out, needle)
            if line:
                print(f"  {line}")

    failures = [r["label"] for r in results if r["exit_code"] not in (0, 1)]
    print(f"\ntotal wall clock  {total / 60:.1f} min over {len(results)} commands")
    print(f"refused/errored   {len(failures)}" + (f" -> {failures}" if failures else ""))
    print("\nexit 1 from `stability`/`compare` means the harness ran and found problems, "
          "which is a result. Exit 2 means it refused.")

    payload = {
        "captured_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "plan": "docs/gpu-shakedown-plan.md",
        "runtime_manifest": str(MANIFEST),
        "declared_deviation": f"top_p={TOP_P} (endpoint rejects the pinned 0)",
        "models": list(CHAT_MODELS),
        "not_tested": {"qwen3-asr-1.7b": "speech model; no audio fixtures in this repo"},
        "total_minutes": round(total / 60, 2),
        "commands": [{k: v for k, v in r.items() if k != "stdout"} for r in results],
    }
    Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0 if not failures else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
