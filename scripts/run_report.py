"""Everything one run directory has to answer for, in one place.

`baseline` prints outcome counts and the backend identity as it goes, but three of the
facts a pinned run has to be judged on are not on that page: the RAW CONTENT of every
schema violation (now that `runner` persists it), `N_flip` (which `stability` computes
and `baseline` does not), and the fabrication rate (which lives in `evalgen.fabrication`
and has no CLI at all). Reading them back off disk rather than recomputing them at run
time is deliberate: these are the numbers that get quoted, and a number quoted from a
different process than the one that wrote the log is a number nobody can re-derive.

Nothing here scores. `n_flip`, `fabrication_rate` and the metric functions are imported
from where they already live; this file is a reader.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from evalgen.cli import load_run, replicate_records  # noqa: E402
from evalgen.console import configure_stdout  # noqa: E402
from evalgen.fabrication import fabrication_rate  # noqa: E402
from evalgen.report import n_flip  # noqa: E402
from evalgen.testsets import load_testset  # noqa: E402

RAW_HEAD = 240


def report(run_dir: Path) -> dict:
    loaded = load_run(run_dir)
    meta = loaded.meta
    rows = [json.loads(line) for line in (run_dir / "run.jsonl").read_text("utf-8").splitlines() if line.strip()]

    testset = load_testset(Path(meta["testset_path"]))
    per_replicate = replicate_records(loaded.result, testset.items)
    flips = n_flip(per_replicate)

    spread = defaultdict(set)
    for row in rows:
        if row.get("prompt_tokens") is not None:
            spread[row["item_id"]].add(row["prompt_tokens"])
    multi = {k: sorted(v) for k, v in spread.items() if len(v) > 1}

    latencies = [r["latency_s"] for r in rows if r.get("latency_s") is not None]
    reasoning = [r["reasoning_tokens"] for r in rows if r.get("reasoning_tokens") is not None]
    violations = [r for r in rows if r["outcome"] != "ok"]

    fab = fabrication_rate(run_dir, Path(meta["gt_path"]))

    print(f"\n{'=' * 78}\n{meta['run_id']}   arm={meta['arm']}  prompt={meta['prompt_id']}\n{'=' * 78}")
    print(f"  model requested   {meta['model_requested']}")
    print(f"  provider          requested={meta.get('provider_requested')!r}  served={meta.get('observed_providers')}")
    print(f"  observed models   {meta.get('observed_models')}")
    print(f"  prompt sha        {meta['prompt_sha'][:16]}")
    print(f"  rows              {meta['rows']}  ({meta['items']} items x {meta['repeats']} replicates)")
    print(f"  outcomes          {meta['outcome_counts']}")
    print(f"  PIN PROOF         {len(spread) - len(multi)}/{len(spread)} items single-valued prompt_tokens"
          + ("  <-- FAILED" if multi else "  (one tokenizer answered)"))
    if multi:
        for k, v in multi.items():
            print(f"      SPLIT {k}: {v}")
    print(f"  reasoning tokens  {dict(Counter(reasoning))}" if len(set(reasoning)) < 6
          else f"  reasoning tokens  min={min(reasoning)} median={statistics.median(reasoning)} max={max(reasoning)}")
    print(f"  latency (s)       median={statistics.median(latencies):.2f}  max={max(latencies):.2f}")
    print(f"  cost (USD, lower) {meta['total_cost_usd_lower_bound']:.6f}")
    print(f"  truncated rate    {meta['truncated_rate']}")
    print(f"  N_flip            {flips}  over {len(per_replicate)} replicates")
    print(f"  FABRICATION       invented={fab['invented']}  example_derived={fab['example_derived']}  "
          f"rate={fab['rate']:.3f}")
    print(f"                    main_slot_invented={fab['main_slot_invented']}  "
          f"also_in_gt_row={fab['also_present_in_gt_row']}  blank_slots_offered={fab['blank_slots_offered']}")
    print(f"                    rows_joined={fab['rows_joined']}  orphans={fab['orphan_product_rows']}  "
          f"skipped_not_parse_ok={fab['records_skipped_not_parse_ok']}")
    print(f"                    emitted={dict(fab['emitted'].most_common(8))}")

    if violations:
        print(f"\n  SCHEMA VIOLATIONS / FAILURES ({len(violations)}), raw content:")
        for row in violations:
            raw = row.get("raw_content")
            shown = "<none captured>" if raw is None else repr(raw[:RAW_HEAD])
            print(f"    {row['item_id']} rep{row['replicate']}  {row['outcome']}  "
                  f"finish={row['finish_reason']}  ct={row['completion_tokens']}  err={row['error']}")
            print(f"      {shown}")
    else:
        print("\n  no failures: every row parsed and satisfied the required keys.")

    return {
        "run_id": meta["run_id"], "arm": meta["arm"], "prompt": meta["prompt_id"],
        "model": meta["model_requested"], "provider": meta.get("provider_requested"),
        "outcomes": meta["outcome_counts"], "n_flip": flips,
        "fab_invented": fab["invented"], "fab_example": fab["example_derived"],
        "fab_rate": fab["rate"], "cost": meta["total_cost_usd_lower_bound"],
        "pin_ok": not multi, "items": len(spread),
        "latency_median": round(statistics.median(latencies), 2),
        "latency_max": round(max(latencies), 2),
    }


if __name__ == "__main__":
    configure_stdout()
    summaries = [report(Path(arg)) for arg in sys.argv[1:]]
    print(f"\n{'=' * 78}\nMACHINE SUMMARY\n{'=' * 78}")
    for summary in summaries:
        print(json.dumps(summary, ensure_ascii=False))
