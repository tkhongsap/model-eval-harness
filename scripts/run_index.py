"""Generate `RUNS.md` — the committed index of every evaluation run.

Why this exists
---------------
`out/` is gitignored, deliberately: `cli.py:135-137` records that run artifacts carry
model output verbatim, so they are written "somewhere already denied rather than
somewhere that has to be remembered". The consequence nobody had written down is that
**every `run_id` quoted in `EXPERIMENTS.md` is a pointer into an untracked directory**.
A reader who clones this repository finds the experiment narrative and none of the runs
it cites, with no way to tell which runs existed, what they were pinned to, or whether
their pin held.

This script closes that gap without weakening the ignore rule. It reads only the
`run.json` PROVENANCE — shas, model, provider, decoding, outcome counts, cost — and
never `run.jsonl`, which is where the model's text lives. `RUNS.md` is therefore
committable for the same reason `run.json` is safe to read: it carries no payload.

What it deliberately does NOT do
--------------------------------
It does not score, re-derive or interpret anything. Every column is copied out of a
`run.json` that a run wrote at the time; nothing here recomputes a number that a run
already recorded, because a second computation is a second answer to reconcile
(`scripts/export_xlsx.py` makes the same argument).

Dry runs are **counted, not dropped.** `_dry_run` returns before the meta block is
written (`cli.py:1032-1036`), so a dry-run directory has `requests.jsonl` and
`prompt.txt` and no `run.json`. Silently skipping them would make the index disagree
with the directory listing for a reason the reader cannot see, so they are reported as
a footnote with their names.

Usage
-----
    python scripts/run_index.py                 # writes RUNS.md at the repo root
    python scripts/run_index.py --check         # exit 1 if RUNS.md is out of date

`--check` exists so the index can be verified rather than trusted: regenerating and
diffing is the only way to know a committed index still describes the runs on disk.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from evalgen.console import configure_stdout  # noqa: E402

DEFAULT_RUNS = REPO / "out" / "runs"
DEFAULT_INDEX = REPO / "RUNS.md"

# Short shas everywhere. The full value is in the run's own run.json; this table exists
# to let a reader spot that two runs share a testset, not to be pasted into a checksum.
_SHA = 12


def _short(value: Any, n: int = _SHA) -> str:
    text = str(value or "")
    return text[:n] if text else "-"


def _outcomes(counts: dict[str, int] | None) -> str:
    """`ok=300` for a clean run, every non-ok outcome named when there is one.

    A clean run prints one token so the column stays readable, but a run with failures
    prints every one of them. Collapsing failures into a total would hide that
    `empty_length` is a run-configuration bug while `empty_other` is a fact about the
    model -- `report.py:727-730` makes the same distinction for the same reason.
    """
    if not counts:
        return "-"
    ok = counts.get("ok", 0)
    bad = {k: v for k, v in sorted(counts.items()) if k != "ok"}
    if not bad:
        return f"ok={ok}"
    return f"ok={ok}, " + ", ".join(f"**{k}={v}**" for k, v in bad.items())


def _pin_proof(meta: dict[str, Any]) -> str:
    """`100/100` single-valued prompt_tokens, or a loud marker when the arm split.

    This is the one column that cannot be faked by the router. Every replicate of an
    item sends a byte-identical request, so `prompt_tokens` is a pure function of the
    backend's tokenizer: two values means two builds served the arm, whatever the
    `provider` field claimed (`runner.RunResult.prompt_token_spread`).

    A run with `repeats < 2` cannot split, and says so rather than printing a clean
    fraction that would read as evidence.
    """
    spread = meta.get("prompt_token_spread") or {}
    split = meta.get("split_items") or {}
    if not spread:
        return "-"
    if int(meta.get("repeats", 0)) < 2:
        return f"n/a ({meta.get('repeats')} rep)"
    checked = sum(1 for values in spread.values() if values)
    if split:
        return f"**SPLIT {checked - len(split)}/{checked}**"
    return f"{checked - len(split)}/{checked}"


def collect(runs_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Every run.json under `runs_dir`, plus the names of directories that had none."""
    rows: list[dict[str, Any]] = []
    dry: list[str] = []
    if not runs_dir.is_dir():
        return rows, dry
    for directory in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        meta_path = directory / "run.json"
        if not meta_path.exists():
            dry.append(directory.name)
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError:
            # A corrupt run.json is reported in the same place as a dry run rather than
            # crashing the index. The index's job is to say what is on disk.
            dry.append(f"{directory.name} (unreadable run.json)")
            continue
        meta.setdefault("run_id", directory.name)
        rows.append(meta)
    rows.sort(key=lambda m: str(m.get("created_utc") or m.get("run_id")))
    return rows, dry


def render(rows: list[dict[str, Any]], dry: list[str]) -> str:
    lines: list[str] = [
        "# Run index",
        "",
        "**Generated by `scripts/run_index.py`. Do not edit by hand — run the script.**",
        "",
        "Every evaluation run this repository has produced, with the provenance needed to",
        "cite one. `out/` is gitignored on purpose (run artifacts carry model output",
        "verbatim, `cli.py:135-137`), so the runs themselves live only on the machine that",
        "made them. This index carries their provenance and none of their payloads, which",
        "is why it can be committed.",
        "",
        "`pin proof` is the column that matters and the only one the router cannot fake:",
        "every replicate of an item sends a byte-identical request, so `prompt_tokens` is a",
        "pure function of the backend's tokenizer. Anything other than `N/N` means the arm",
        "was served by more than one build and its numbers are a blend of two systems.",
        "",
        "`cost` is a LOWER BOUND — OpenRouter reports `usage.cost` only for providers that",
        "supply it, and a missing value stays absent rather than becoming zero.",
        "",
        f"**{len(rows)} runs recorded.**",
        "",
        "| run_id | arm | model requested | provider | prompt | items x reps | outcomes | pin proof | cost USD | testset | scorer |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m in rows:
        cost = m.get("total_cost_usd_lower_bound")
        lines.append(
            f"| `{m.get('run_id')}` "
            f"| {m.get('arm', '-')} "
            f"| `{m.get('model_requested', '-')}` "
            f"| {m.get('provider_requested') or '*unpinned*'} "
            f"| {m.get('prompt_id', '-')} "
            f"| {m.get('items', '-')} x {m.get('repeats', '-')} "
            f"| {_outcomes(m.get('outcome_counts'))} "
            f"| {_pin_proof(m)} "
            f"| {f'{cost:.6f}' if isinstance(cost, (int, float)) else '-'} "
            f"| `{_short(m.get('testset_sha'))}` "
            f"| `{_short(m.get('scorer_sha'), 8)}` |"
        )

    total = sum(
        m["total_cost_usd_lower_bound"]
        for m in rows
        if isinstance(m.get("total_cost_usd_lower_bound"), (int, float))
    )
    calls = sum(int(m.get("rows") or 0) for m in rows)
    lines += [
        "",
        f"**Total recorded spend: ${total:.4f} (lower bound) over {calls} calls.**",
        "",
    ]

    if dry:
        lines += [
            "## Directories with no `run.json`",
            "",
            "Dry runs write `requests.jsonl` and `prompt.txt` and return before the meta",
            "block (`cli.py:1032-1036`). Listed rather than skipped, so this index agrees",
            "with the directory listing:",
            "",
        ]
        lines += [f"- `{name}`" for name in dry]
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(prog="run_index", description=__doc__)
    parser.add_argument("--runs", default=str(DEFAULT_RUNS))
    parser.add_argument("--out", default=str(DEFAULT_INDEX))
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if the file on disk differs from what would be written",
    )
    args = parser.parse_args(argv)

    rows, dry = collect(Path(args.runs))
    text = render(rows, dry)
    target = Path(args.out)

    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current == text:
            print(f"{target.name} is up to date ({len(rows)} runs).")
            return 0
        print(
            f"{target.name} is OUT OF DATE. Regenerate with: "
            f"python scripts/run_index.py",
            file=sys.stderr,
        )
        return 1

    target.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {target}  ({len(rows)} runs, {len(dry)} dir(s) without run.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
