"""Collect the GPU metrics the soak test cannot reach from outside.

Run this ON the GPU host (10.94.154.102) while `scripts/soak_test.py` runs from anywhere. It
writes a JSONL that `scripts/soak_report.py` time-joins with the request log, filling in the
four metrics that are otherwise absent from the report: **GPU utilization, VRAM, temperature
and power.**

    python3 gpu_telemetry.py --out gpu.jsonl                 # nvidia-smi, every 5s
    python3 gpu_telemetry.py --out gpu.jsonl --vllm http://127.0.0.1:8000/metrics
    python3 gpu_telemetry.py --out gpu.jsonl --duration 18000   # 5 hours, then stop

Then, back where the soak ran:

    cp gpu.jsonl out/soak/<run>/gpu.jsonl
    python scripts/soak_report.py out/soak/<run>

**Standard library only, Python 3.8+.** No pip install, nothing to provision -- it has to run
on a machine nobody wants to change.

Why this exists
---------------
Measured 2026-08-15 against Token Factory: the LiteLLM virtual key is scoped to
`['llm_api_routes']`, so `/metrics` is 401 and `/health` is 403. That only blocks *gateway*
counters. GPU telemetry is a separate problem: LiteLLM is a proxy and never sees a GPU at all.

The response headers name the topology exactly:

    x-litellm-model-api-base: http://127.0.0.1:8000/v1
    system_fingerprint:       vllm-0.23.1rc1.dev245+g9037498c2

vLLM runs on **loopback on the same host as the gateway**, so its `/metrics` -- which carries
`vllm:gpu_cache_usage_perc`, `num_requests_running`, `num_requests_waiting` and the preemption
counters -- is unreachable from outside and is not proxied by nginx (every `*metrics*` path
resolves to LiteLLM's own guarded endpoint). Nothing a client can do reaches it. One command on
the box does.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request

# nvidia-smi query fields -> the names used in the report.
FIELDS = [
    ("index", int), ("utilization.gpu", float), ("utilization.memory", float),
    ("memory.used", float), ("memory.total", float), ("temperature.gpu", float),
    ("power.draw", float), ("power.limit", float), ("clocks.sm", float),
]
QUERY = ",".join(name for name, _ in FIELDS)

# The vLLM families worth keeping. gpu_cache_usage_perc is the KV-cache occupancy and is the
# closest thing to a "how close are we to OOM" signal; the preemption counter is how cache
# exhaustion actually manifests short of a crash.
VLLM_KEEP = (
    "vllm:gpu_cache_usage_perc", "vllm:cpu_cache_usage_perc",
    "vllm:num_requests_running", "vllm:num_requests_waiting", "vllm:num_requests_swapped",
    "vllm:num_preemptions_total", "vllm:gpu_prefix_cache_hit_rate",
)


def sample_nvidia_smi() -> list[dict] | None:
    """One row per GPU, or None if nvidia-smi is not present."""
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-gpu={QUERY}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    rows = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != len(FIELDS):
            continue
        row = {}
        for (name, cast), raw in zip(FIELDS, parts):
            try:
                row[name.replace(".", "_")] = cast(raw)
            except (TypeError, ValueError):
                row[name.replace(".", "_")] = None   # "[N/A]" on some virtualised GPUs
        used, total = row.get("memory_used"), row.get("memory_total")
        row["memory_used_pct"] = round(100 * used / total, 2) if used and total else None
        rows.append(row)
    return rows


def scrape_prometheus(url: str, keep: tuple[str, ...]) -> dict:
    """Pull selected families out of a Prometheus exposition. No client library needed."""
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            body = response.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    out: dict[str, float] = {}
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        name = line.split("{", 1)[0].split(" ", 1)[0]
        if name not in keep:
            continue
        try:
            out[name] = float(line.rsplit(" ", 1)[1])
        except (IndexError, ValueError):
            continue
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gpu_telemetry", description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="gpu.jsonl")
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between samples")
    ap.add_argument("--duration", type=float, default=0.0, help="seconds; 0 = until Ctrl-C")
    ap.add_argument("--vllm", default=None,
                    metavar="URL", help="e.g. http://127.0.0.1:8000/metrics")
    ap.add_argument("--dcgm", default=None,
                    metavar="URL", help="DCGM exporter, e.g. http://127.0.0.1:9400/metrics")
    args = ap.parse_args(argv)

    probe = sample_nvidia_smi()
    if probe is None:
        print("nvidia-smi not found or not runnable. This script must run ON the GPU host.",
              file=sys.stderr)
        print("If the GPU metrics come from DCGM instead, pass --dcgm and ignore this.",
              file=sys.stderr)
        if not args.dcgm and not args.vllm:
            return 2
    else:
        print(f"nvidia-smi: {len(probe)} GPU(s) detected", file=sys.stderr)

    deadline = time.time() + args.duration if args.duration else None
    written = 0
    with open(args.out, "a", encoding="utf-8") as handle:
        try:
            while deadline is None or time.time() < deadline:
                row: dict = {"ts": time.time()}
                gpus = sample_nvidia_smi()
                if gpus is not None:
                    row["gpus"] = gpus
                    # Whole-host rollup, so the report has one number per instant even on a
                    # multi-GPU box.
                    util = [g["utilization_gpu"] for g in gpus if g.get("utilization_gpu") is not None]
                    used = [g["memory_used"] for g in gpus if g.get("memory_used") is not None]
                    tot = [g["memory_total"] for g in gpus if g.get("memory_total") is not None]
                    temp = [g["temperature_gpu"] for g in gpus if g.get("temperature_gpu") is not None]
                    pwr = [g["power_draw"] for g in gpus if g.get("power_draw") is not None]
                    row["rollup"] = {
                        "util_pct_mean": round(sum(util) / len(util), 1) if util else None,
                        "util_pct_max": max(util) if util else None,
                        "vram_used_mib": round(sum(used), 1) if used else None,
                        "vram_total_mib": round(sum(tot), 1) if tot else None,
                        "vram_used_pct": round(100 * sum(used) / sum(tot), 2)
                        if used and tot and sum(tot) else None,
                        "temp_c_max": max(temp) if temp else None,
                        "power_w_total": round(sum(pwr), 1) if pwr else None,
                    }
                if args.vllm:
                    row["vllm"] = scrape_prometheus(args.vllm, VLLM_KEEP)
                if args.dcgm:
                    row["dcgm"] = scrape_prometheus(args.dcgm, (
                        "DCGM_FI_DEV_GPU_UTIL", "DCGM_FI_DEV_FB_USED", "DCGM_FI_DEV_FB_FREE",
                        "DCGM_FI_DEV_GPU_TEMP", "DCGM_FI_DEV_POWER_USAGE",
                        "DCGM_FI_DEV_MEM_COPY_UTIL"))
                handle.write(json.dumps(row) + "\n")
                handle.flush()          # a 5-hour collector must survive being killed
                written += 1
                if written % 60 == 0:
                    roll = row.get("rollup") or {}
                    print(f"  {written} samples · util {roll.get('util_pct_mean')}% · "
                          f"vram {roll.get('vram_used_pct')}% · {roll.get('temp_c_max')}C · "
                          f"{roll.get('power_w_total')}W", file=sys.stderr)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass
    print(f"wrote {written} samples to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
