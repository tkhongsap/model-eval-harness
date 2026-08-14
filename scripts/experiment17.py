"""Reproduce Experiment 17: Token Factory's two GPU models against Gemini, on E7's pack.

The contract, the deviations and the reading order are in `docs/experiment17-plan.md`. Read
that first; this file executes it and does not restate it.

**Every measurement goes through `scripts/evalgen.py`.** Nothing here re-implements a call
loop, a scorer or a metric -- same argument as `scripts/gpu_shakedown.py`, which this follows.

Honest provenance: the 2026-08-14 execution was driven command by command, not by this script.
This encodes those exact commands so the experiment can be repeated -- E7's own plan names a
phase three, and there will be a phase four.

Two things it does that the shakedown script does not, both because they are mistakes that
were available to make here:

  1. **`SSL_CERT_FILE` is set per command, never process-wide.** It pins trust to Token
     Factory's self-signed certificate, which is right for the GPU arms and *wrong* for the
     OpenRouter arm -- it would replace the public trust store and break the Gemini call.
     `gpu_shakedown.py` sets it in `_env()` for every child because every child was a GPU call;
     copying that here would have broken the incumbent.

  2. **It refuses to start, and refuses to finish, if `scoring_code_sha` has moved.** All three
     arms must carry the same value or `compare` rejects the pair (`manifest.py:170`), and the
     failure surfaces only after every call is paid for. Experiment 5.1 was lost to exactly
     this: a docs-adjacent commit moved the digest mid-experiment and $0.49 bought nothing
     (`EXPERIMENTS.md:841-853`).

Usage:
    python scripts/experiment17.py                 # control, 3 arms, 2 compares (~70 min)
    python scripts/experiment17.py --control-only  # free: re-score the E10 pair at HEAD
    python scripts/experiment17.py --skip-gemini   # reuse an existing incumbent run
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
REPORTS = REPO_ROOT / "out" / "reports"

# retention_v3, NOT the retention_v1 that DEFAULT_TESTSET points at (cli.py:172-173).
# Passing these explicitly is not belt and braces: omitting them yields a silent 20-item run
# that scores cleanly and cannot compare to anything in this experiment.
TESTSET = "tests/fixtures/testsets/retention_v3.jsonl"
GT = "tests/fixtures/testsets/retention_v3.gt.csv"
PROMPT_ID = "v9_16_base"
REPEATS = "3"

# Declared in the plan: the endpoint rejects the pinned 0 with `400 top_p must be in (0, 1]`.
# At temperature 0 the decode is greedy, so this is inert -- and it is the value E10's Gemini
# already used, so it introduces no asymmetry between the arms.
TOP_P = "1.0"

GPU_ARMS = (("e17-tf-gemma", "gemma-4-12b-it"), ("e17-tf-qwen", "qwen3.6-27b-fp8"))

# Both carry scoring_code_sha cefd4ae9..., so they compare to each other legally at any HEAD.
# Re-scoring them today separates "did the scorer change" from "did the model change"; without
# it those two are confounded for the whole experiment. Free -- no model calls.
CONTROL = (
    "out/runs/20260810-165653Z-e10-gemini-base",
    "out/runs/20260810-165637Z-e10-gemma-base",
)


def _digest() -> str:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from evalharness import manifest  # noqa: PLC0415 - after the path is set

    return manifest.scoring_code_sha()


def _env(*, pin_cert: bool) -> dict[str, str]:
    """Child environment. `pin_cert` is the whole point -- see the module docstring."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("EVAL_HARNESS_KEY_HMAC", "experiment-17")
    if pin_cert:
        env["SSL_CERT_FILE"] = str(CERT)
    else:
        env.pop("SSL_CERT_FILE", None)
    return env


def run(label: str, args: list[str], *, pin_cert: bool, timeout: float = 7200) -> dict:
    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
    before = set(RUNS.iterdir()) if RUNS.exists() else set()
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(EVALGEN), *args],
        cwd=REPO_ROOT, env=_env(pin_cert=pin_cert), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    elapsed = time.monotonic() - started
    created = sorted(set(RUNS.iterdir()) - before) if RUNS.exists() else []
    print("\n".join((proc.stdout or "").strip().splitlines()[-12:]))
    if proc.returncode != 0:
        print(f"  exit={proc.returncode}")
        # In full, never filtered. A grep-narrowed failure hid a real one during the
        # shakedown; the tail of stderr is the cheapest thing to be wrong about.
        print((proc.stderr or "").strip())
    print(f"  [{elapsed / 60:.1f} min]")
    return {
        "label": label, "exit_code": proc.returncode, "minutes": round(elapsed / 60, 2),
        "run_dir": str(created[-1]) if created else None, "stdout": proc.stdout,
    }


def _arm_args(arm: str, model: str, *, gpu: bool) -> list[str]:
    common = [
        "baseline", "--arm", arm, "--model", model,
        "--testset", TESTSET, "--gt", GT, "--prompt-id", PROMPT_ID,
        "--repeats", REPEATS, "--top-p", TOP_P,
        # 3 on every arm, deliberately. Pinning 1 on the GPU arms alone while the incumbent
        # kept 3 would let a transient gateway blip depress GPU F1 against a Gemini that got
        # retries. Reliability is read off outcome_counts and attempt_count instead.
        "--max-attempts", "3",
    ]
    if gpu:
        return [*common, "--runtime-manifest", str(MANIFEST), "--concurrency", "4"]
    # Matched to 20260810-165653Z-e10-gemini-base knob for knob, so the re-run doubles as an
    # invariance control: same model, same config, four days apart.
    return [*common, "--provider", "Google", "--concurrency", "8", "--reasoning-effort", "none"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="experiment17")
    parser.add_argument("--control-only", action="store_true", help="the free re-score, then stop")
    parser.add_argument("--skip-gemini", metavar="RUN_DIR", default=None,
                        help="reuse an incumbent run instead of paying for another")
    parser.add_argument("--out", default="experiment17_results.json")
    args = parser.parse_args(argv)

    if not CERT.is_file() or not MANIFEST.is_file():
        print(f"missing {CERT} or {MANIFEST}")
        return 2

    opening_digest = _digest()
    print(f"scoring_code_sha at start  {opening_digest}")
    REPORTS.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    # --- the control: today's scorer over the 2026-08-10 outputs, no model calls -----
    incumbent, candidate = CONTROL
    results.append(run(
        "control  E10 pair re-scored at HEAD (no model calls)",
        ["compare", "--incumbent", incumbent, "--candidate", candidate,
         "--report", str(REPORTS / "control-e10-rescored-at-head.txt")],
        pin_cert=False,
    ))
    if args.control_only:
        return 0

    # --- the incumbent, first: cheapest, and it proves the pipeline before the GPU hour ---
    if args.skip_gemini:
        gemini_dir = args.skip_gemini
    else:
        r = run("arm  e17-gemini  (OpenRouter, provider Google)",
                _arm_args("e17-gemini", "google/gemini-2.5-flash", gpu=False),
                pin_cert=False)
        results.append(r)
        gemini_dir = r["run_dir"]
    if not gemini_dir:
        print("no incumbent run directory; refusing to spend on the GPU arms")
        return 1

    # --- the GPU arms, SEQUENTIALLY ---------------------------------------------------
    # Not concurrently. Both would contend for the same gateway -- the shakedown measured c8
    # at 0.96x c4, so the gateway saturates around there -- and each arm's latency would then
    # be a measurement of the other arm. Total wall clock is about the same either way.
    gpu_dirs: dict[str, str | None] = {}
    for arm, model in GPU_ARMS:
        r = run(f"arm  {arm}  ({model}, Token Factory fp8)",
                _arm_args(arm, model, gpu=True), pin_cert=True)
        results.append(r)
        gpu_dirs[arm] = r["run_dir"]

    # --- the comparisons ---------------------------------------------------------------
    for arm, directory in gpu_dirs.items():
        if not directory:
            continue
        results.append(run(
            f"compare  e17-gemini vs {arm}",
            ["compare", "--incumbent", gemini_dir, "--candidate", directory,
             "--report", str(REPORTS / f"compare-e17-{arm.replace('e17-tf-', '')}.txt")],
            pin_cert=False,
        ))

    closing_digest = _digest()
    moved = closing_digest != opening_digest
    print(f"\n{'=' * 78}\nscoring_code_sha at end    {closing_digest}")
    if moved:
        print("  *** IT MOVED MID-EXPERIMENT. The arms are no longer mutually comparable and")
        print("      any compare above that succeeded did so before the change. See E5.1.")

    payload = {
        "captured_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "plan": "docs/experiment17-plan.md",
        "scoring_code_sha": {"start": opening_digest, "end": closing_digest, "moved": moved},
        "testset": TESTSET, "prompt_id": PROMPT_ID, "repeats": int(REPEATS),
        "declared_deviations": [
            f"top_p={TOP_P}; the endpoint rejects the pinned 0 and greedy decode makes it inert",
            "reasoning_effort: none on Gemini, provider-default on Token Factory",
            "fp8 quantisation; not the same deployments as E7 or E10 despite similar names",
        ],
        "not_tested": {"qwen3-asr-1.7b": "speech model; no audio fixtures in this repo"},
        "commands": [{k: v for k, v in r.items() if k != "stdout"} for r in results],
    }
    Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")

    failures = [r["label"] for r in results if r["exit_code"] not in (0, 1)]
    if failures:
        print(f"refused/errored: {failures}")
    print("\nexit 1 from `compare` means the harness ran and found problems, which is a "
          "result. Exit 2 means it refused.")
    return 1 if (failures or moved) else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
