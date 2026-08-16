"""Experiment 20: Gemini vs Qwen3.8 vs Gemma 4 on `retention_challenge_v1`.

The pack has never been evaluated by any model (verified: its `testset_sha`
`a3029a70...` appears in no `out/runs/*/run.json`). It is the only unevaluated runnable
pack in the repository -- the four `block_*.jsonl` drafts have no ground truth and fail the
label contract in 155 places.

Why this pack is worth the spend. `retention_v3` tests label semantics one axis at a time
(Thai linguistics, ASR artifacts, code-switch, context dilation). This one tests
*interaction structure*: prior-contact history, mid-call reversal, competing issues,
interruption and topic return, and explicit end-state negotiation. Every item is exactly 18
turns. It is also far denser in the thing models actually fail at -- **11 of 50 calls carry
more than one product** (three of them carry three), against 8 of 138 in v3.

Three arms, not four. `qwen3.6-27b-fp8` was replaced by `qwen3.8-27b-fp8` on the GPU host
between 05:48Z and 12:45Z on 2026-08-15 and is no longer deployed, so it cannot be run on a
new pack. Confirmed live against `/v1/models` before writing this.

Follows `scripts/experiment17.py` exactly, including the two things it does that
`gpu_shakedown.py` does not:

  1. **`SSL_CERT_FILE` is set per command, never process-wide.** It pins trust to Token
     Factory's self-signed certificate, which is right for the GPU arms and *wrong* for the
     OpenRouter arm -- it would replace the public trust store and break the Gemini call.

  2. **It refuses to start, and refuses to finish, if `scoring_code_sha` has moved.** All
     three arms must carry the same value or `compare` rejects the pair
     (`manifest.py:170`), and the failure surfaces only after every call is paid for.
     Experiment 5.1 was lost to exactly this (`EXPERIMENTS.md:841-853`).

And one thing E17 did not need: `EVAL_HARNESS_KEY_HMAC` is **absent from `.env`**. It is
read only by `compare` (`cli.py:3468`), so without it every arm would run, every call would
be paid for, and the comparison would refuse afterwards. Set here with `setdefault`.

Usage:
    python scripts/experiment20.py                # smoke, 3 arms, 2 compares (~18 min)
    python scripts/experiment20.py --smoke-only   # the cheap pre-flight, then stop
    python scripts/experiment20.py --skip-smoke   # straight to the arms
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

# The challenge pack, NOT the retention_v1 that DEFAULT_TESTSET points at (cli.py:172-173).
# Passing these explicitly is not belt and braces: omitting them yields a silent 20-item run
# that scores cleanly and cannot compare to anything in this experiment.
TESTSET = "tests/fixtures/testsets/retention_challenge_v1.jsonl"
GT = "tests/fixtures/testsets/retention_challenge_v1.gt.csv"
PROMPT_ID = "v9_16_base"
REPEATS = "3"

# The endpoint rejects the pinned 0 with `400 top_p must be in (0, 1]`. At temperature 0 the
# decode is greedy, so this is inert -- and it is the value every E17/E18 arm already used,
# so it introduces no asymmetry.
TOP_P = "1.0"

GEMINI_ARM = ("e20-chal-gemini", "google/gemini-2.5-flash")
GPU_ARMS = (("e20-chal-qwen38", "qwen3.8-27b-fp8"), ("e20-chal-gemma", "gemma-4-12b-it"))

EXPECTED_DIGEST = "9b4afc95c3d698761cebcfd19e7c9c04fa3e5a850a015d291fc21d8e5e900db3"


def _digest() -> str:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from evalharness import manifest  # noqa: PLC0415 - after the path is set

    return manifest.scoring_code_sha()


def _env(*, pin_cert: bool) -> dict[str, str]:
    """Child environment. `pin_cert` is the whole point -- see the module docstring."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("EVAL_HARNESS_KEY_HMAC", "experiment-20")
    if pin_cert:
        env["SSL_CERT_FILE"] = str(CERT)
    else:
        env.pop("SSL_CERT_FILE", None)
    return env


def run(label: str, args: list[str], *, pin_cert: bool, timeout: float = 7200) -> dict:
    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}", flush=True)
    before = set(RUNS.iterdir()) if RUNS.exists() else set()
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(EVALGEN), *args],
        cwd=REPO_ROOT, env=_env(pin_cert=pin_cert), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    elapsed = time.monotonic() - started
    created = sorted(set(RUNS.iterdir()) - before) if RUNS.exists() else []
    print("\n".join((proc.stdout or "").strip().splitlines()[-14:]), flush=True)
    if proc.returncode != 0:
        print(f"  exit={proc.returncode}", flush=True)
        # In full, never filtered. A grep-narrowed failure hid a real one during the
        # shakedown; the tail of stderr is the cheapest thing to be wrong about.
        print((proc.stderr or "").strip(), flush=True)
    print(f"  [{elapsed / 60:.1f} min]", flush=True)
    return {
        "label": label, "exit_code": proc.returncode, "minutes": round(elapsed / 60, 2),
        "run_dir": str(created[-1]) if created else None, "stdout": proc.stdout,
    }


def _all_ok(run_dir: str | None) -> bool:
    """Did every call in that run actually succeed?

    THE EXIT CODE IS NOT THE CHECK, and this cost a run to learn. `evalgen stability` exits 0
    when every one of its calls fails at the transport layer -- it ran fine, the model did
    not. On 2026-08-15 the Gemma smoke came back `transport_error=3`, this script reported
    "all three smokes passed" because the process exited 0, and the full 150-call arm then
    spent 11 minutes rediscovering the same dead backend one 500 at a time.

    The pre-flight had the answer. The gate threw it away.
    """
    if not run_dir:
        return False
    meta = json.loads((Path(run_dir) / "run.json").read_text(encoding="utf-8"))
    counts = meta.get("outcome_counts") or {}
    bad = {k: v for k, v in counts.items() if k != "ok"}
    if bad:
        print(f"  outcome_counts = {counts}  <-- not clean")
        return False
    return bool(counts.get("ok"))


def _longest_items(n: int = 3) -> list[str]:
    """The n longest transcripts -- the worst case for timeout and truncation.

    E17's smoke used the three longest items in its pack for exactly this reason. Computed
    rather than hardcoded so the smoke stays honest if the pack is ever revised.
    """
    items = [
        json.loads(line)
        for line in (REPO_ROOT / TESTSET).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ranked = sorted(items, key=lambda i: len(i["transcript_th"]), reverse=True)
    return [i["item_id"] for i in ranked[:n]]


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
        # --provider and --reasoning-effort are REFUSED for openai-compatible runtimes
        # (cli.py:1656-1666). Concurrency 4 matches E17/E18 so the numbers stay comparable.
        return [*common, "--runtime-manifest", str(MANIFEST), "--concurrency", "4"]
    return [*common, "--provider", "Google", "--concurrency", "8", "--reasoning-effort", "none"]


def _smoke_args(arm: str, model: str, items: list[str], *, gpu: bool) -> list[str]:
    common = [
        "stability", "--arm", arm, "--model", model,
        "--items", ",".join(items), "--repeats", "1",
        "--testset", TESTSET, "--gt", GT, "--prompt-id", PROMPT_ID, "--top-p", TOP_P,
    ]
    if gpu:
        return [*common, "--runtime-manifest", str(MANIFEST), "--concurrency", "4"]
    return [*common, "--provider", "Google", "--concurrency", "8", "--reasoning-effort", "none"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="experiment20")
    parser.add_argument("--smoke-only", action="store_true", help="cheap pre-flight, then stop")
    parser.add_argument("--skip-smoke", action="store_true", help="straight to the full arms")
    args = parser.parse_args(argv)

    REPORTS.mkdir(parents=True, exist_ok=True)
    started_digest = _digest()
    print(f"scoring_code_sha at start: {started_digest}")
    if started_digest != EXPECTED_DIGEST:
        print(f"  REFUSING: expected {EXPECTED_DIGEST}")
        print("  E17 and E18 carry that digest. A different one means these arms cannot be")
        print("  compared against them, and `compare` would reject the pair after the spend.")
        return 2

    steps: list[dict] = []

    if not args.skip_smoke:
        items = _longest_items()
        print(f"smoke items (3 longest transcripts): {', '.join(items)}")
        for arm, model in (GEMINI_ARM, *GPU_ARMS):
            gpu = (arm, model) != GEMINI_ARM
            r = run(f"SMOKE {arm} ({model})",
                    _smoke_args(f"e20-smoke-{arm.split('-')[-1]}", model, items, gpu=gpu),
                    pin_cert=gpu, timeout=900)
            steps.append(r)
            if r["exit_code"] != 0 or not _all_ok(r["run_dir"]):
                print(f"\nSMOKE FAILED for {arm}. Stopping before the full spend.")
                return 1
        print("\nAll smokes passed.")
        if args.smoke_only:
            return 0

    # Gemini first (fast, and it is the incumbent every compare needs), then the GPU arms
    # SEQUENTIALLY -- they contend for one gateway, and run together each arm's latency
    # becomes a measurement of the other (experiment17.py:178-181).
    arm_dirs: dict[str, str] = {}
    for arm, model in (GEMINI_ARM, *GPU_ARMS):
        gpu = (arm, model) != GEMINI_ARM
        r = run(f"ARM {arm} ({model})", _arm_args(arm, model, gpu=gpu), pin_cert=gpu)
        steps.append(r)
        if r["exit_code"] != 0 or not r["run_dir"]:
            print(f"\nARM {arm} FAILED. Stopping.")
            return 1
        arm_dirs[arm] = r["run_dir"]

    # Two compares, each GPU arm against Gemini as incumbent. Argument order determines the
    # paired verdict direction, so the incumbent is never passed as --candidate.
    incumbent = arm_dirs[GEMINI_ARM[0]]
    for arm, _model in GPU_ARMS:
        short = arm.replace("e20-chal-", "")
        report = REPORTS / f"compare-e20-{short}-vs-gemini.txt"
        r = run(f"COMPARE {short} vs gemini",
                ["compare", "--incumbent", incumbent, "--candidate", arm_dirs[arm],
                 "--report", str(report)],
                pin_cert=False, timeout=1800)
        steps.append(r)
        # Exit 1 means the harness ran and found problems -- that is a result, not a failure.
        # Exit 2 is a refusal.
        if r["exit_code"] not in (0, 1):
            print(f"\nCOMPARE {short} REFUSED (exit {r['exit_code']}). Stopping.")
            return 1

    ended_digest = _digest()
    print(f"\nscoring_code_sha at end: {ended_digest}")
    if ended_digest != started_digest:
        print("  *** IT MOVED MID-EXPERIMENT *** the arms are not mutually comparable.")
        return 1

    summary = {
        "experiment": "retention-e20",
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "testset": TESTSET,
        "scoring_code_sha": started_digest,
        "arms": arm_dirs,
        "steps": [{k: v for k, v in s.items() if k != "stdout"} for s in steps],
    }
    out = REPORTS / "experiment20-steps.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    print("\nArms:")
    for arm, directory in arm_dirs.items():
        print(f"  {arm:20} {Path(directory).name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
