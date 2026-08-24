"""Emit `docs/reports/usecase-matrix.json` -- one column per (use case, arm), token-costed.

WHAT THIS ADDS over `parallel-eval-workbook.json`: the workbook prints tokens as prose, not as
numbers. Cells read "450,334 label + 471,549 analysis" for a two-stage internal pipeline, and
"176,626 (+ 227,974 thinking)" for the incumbent. Costing a run means parsing those, and parsing
them wrong is silent.

So every arm's tokens are reconciled against the workbook's OWN "Total tokens" row wherever it
publishes one:

    input + output + thinking  ==  published total

Three tabs publish that row and all three reconcile exactly. If a future revision breaks the
identity, `reconciled: false` appears in the output and the report refuses to print a cost for
that arm rather than printing a wrong one.

THE THINKING-TOKEN TRAP, which the reconciliation is what caught. On Sentiment MNP the incumbent
reports 176,626 output tokens and 227,974 thinking tokens, and the published total is 1,924,125
= 1,519,525 + 176,626 + 227,974. The thinking tokens are therefore NOT inside the printed output
line but ARE inside the total. Costing "output x output-rate" would undercount that run's
billable output by 129%. Whether thinking is billed at the output rate is a pricing question,
settled in `openrouter-pricing.json`, not here.

COSTING. Every incumbent run gets one figure: input at the applicable rate, plus output plus
thinking at the output rate. A run that feeds audio into the model is priced at the AUDIO input
rate, 3.3x the text rate on 2.5 Flash, because the audio is what its input total is mostly made
of; the system prompt inside that total is text and cheaper, so the figure is an upper bound and
`cost_usd_floor` carries the all-text lower bound beside it. The split cannot be recovered from
a run aggregate, which publishes one input number.

RTR-Fraud names no model for its incumbent column, so its rate comes from an assumption recorded
in `openrouter-pricing.json` and the arm is flagged `model_assumed` -- a figure resting on a guess
about which model ran is marked as one. Self-hosted arms are `not-metered`, which is not the
same as free: it means no invoice, and the GPU hours behind them are real.

Usage:
    python scripts/usecase_matrix_figures.py            # write
    python scripts/usecase_matrix_figures.py --check    # staleness only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "docs" / "reports" / "parallel-eval-workbook.json"
PRICING = REPO / "docs" / "reports" / "openrouter-pricing.json"
OUT = REPO / "docs" / "reports" / "usecase-matrix.json"

GENERATED = "2026-08-24"

# THE UNIT A COST-PER-ITEM FIGURE IS DIVIDED BY, settled per use case against the workbook and
# production's own scorer. `pin` forces one denominator across every arm; without it each arm
# divides by its own count.
#
# RTR-Fraud is pinned because its workbook tab publishes "Model calls" -- 198 for the incumbent
# and 594 for both internal arms -- and 594 is a REQUEST count, not an item count. Every
# accuracy cell on that tab is scored x/198 for all three arms, and production's scorer works
# one row per RTR_Code (production-reference/rtr-fraud-validation-main/app/modules/
# fact_checker.py:304-338). Letting an arm divide by 594 would understate its per-item cost by
# exactly 3x the moment any rate exists for it.
#
# The unit is a SUBMISSION, not a photo: one RTR_Code folder holds up to three photos and the
# corpus never publishes how many, so no per-photo figure is derivable.
#
# Tax-Invoice is pinned to PAGES, not the 32 files in the same cell. The published per-page
# latencies confirm pages is the divisor the workbook itself used: 389.3/147 = 2.6,
# 8377.7/147 = 57.0, 11715.9/147 = 79.7. Calling it "per document" would understate by 4.59x.
COST_UNIT = {
    "RTR-Fraud": {"unit": "submission", "pin": 198},
    "Tax-Invoice": {"unit": "page", "pin": 147},
    "Sentiment QA": {"unit": "call"},
    "Sentiment MNP": {"unit": "call"},
    "Sentiment Telesale": {"unit": "call"},
    "Sentiment Retention": {"unit": "call"},
}


def parse_tokens(cell: str) -> dict:
    """Pull every number out of a token cell and label the parts.

    Shapes seen in the workbook:
        '1,536,808'                            -> one figure
        '450,334 label  +  471,549 analysis'   -> two stages, summed
        '176,626  (+ 227,974 thinking)'        -> a total plus a SEPARATE thinking figure
        '7791855'                              -> no thousands separators
    """
    t = (cell or "").strip()
    if not t:
        return {"total": None, "parts": [], "thinking": None, "raw": ""}

    thinking = None
    m = re.search(r"\(\s*\+\s*([\d,]+)\s*thinking\s*\)", t, re.I)
    if m:
        thinking = int(m.group(1).replace(",", ""))
        t = t[: m.start()] + t[m.end():]

    parts = []
    for num, label in re.findall(r"([\d,]+)\s*([A-Za-z]*)", t):
        n = num.replace(",", "")
        if not n.isdigit():
            continue
        parts.append({"n": int(n), "label": label.lower() or None})
    if not parts:
        return {"total": None, "parts": [], "thinking": thinking, "raw": cell}
    return {"total": sum(p["n"] for p in parts), "parts": parts,
            "thinking": thinking, "raw": cell}


def seconds(cell: str) -> float | None:
    if not cell:
        return None
    m = re.match(r"^\s*([\d,]+\.?\d*)", str(cell))
    return float(m.group(1).replace(",", "")) if m else None


def items(cell: str) -> tuple[int | None, str | None]:
    """The number of things this run was scored over, and what they are.

    Tax-Invoice states "32 files, 147 pages". The leading number is files, but the run is
    scored per line item and its latency row is PER PAGE, so quoting 32 beside a per-page
    latency puts two different units in the same column. Pages win where both are given.
    """
    t = str(cell or "").strip()
    if not t:
        return None, None
    pages = re.search(r"([\d,]+)\s*pages?\b", t, re.I)
    if pages:
        return int(pages.group(1).replace(",", "")), "pages"
    m = re.match(r"^\s*([\d,]+)", t)
    return (int(m.group(1).replace(",", "")), None) if m else (None, None)


def model_id(arm: str, tab: dict) -> str:
    """The model id the workbook states for this arm, falling back to the column header."""
    row = next((r for r in tab["rows"] if r["label"] == "Model"), None)
    if row:
        v = (row["values"].get(arm) or "").strip()
        if v:
            return v
    return re.sub(r"\s+", " ", arm).strip()


def load_pricing() -> dict:
    if not PRICING.is_file():
        return {"rates": {}, "status": "not established",
                "note": "docs/reports/openrouter-pricing.json is absent, so no run is costed."}
    return json.loads(PRICING.read_text(encoding="utf-8"))


def build() -> dict:
    book = json.loads(SRC.read_text(encoding="utf-8"))
    pricing = load_pricing()
    rates = {k.lower(): v for k, v in (pricing.get("rates") or {}).items()}

    columns: list[dict] = []
    for tab_name, tab in book["tabs"].items():
        arms = [c for c in tab["arms"] if c.strip().upper() != "GROUND TRUTH"]
        inc = [c for c in arms if "GEMINI" in c.upper()]
        cand = [c for c in arms if "GEMINI" not in c.upper()]
        by_label = {}
        for r in tab["rows"]:
            by_label.setdefault(r["label"], r)

        def cell(label: str, arm: str) -> str:
            r = by_label.get(label)
            return (r["values"].get(arm) if r else "") or ""

        for arm in inc + cand:
            is_inc = arm in inc
            tin = parse_tokens(cell("Total Input", arm))
            tout = parse_tokens(cell("Total Output", arm))
            published = parse_tokens(cell("Total tokens", arm))

            billable_out = (tout["total"] or 0) + (tout["thinking"] or 0)
            computed = (tin["total"] or 0) + billable_out

            reconciled = None
            if published["total"] is not None and tin["total"] is not None:
                reconciled = computed == published["total"]

            items_n, items_unit = items(
                cell("Calls scored", arm) or cell("Model calls", arm)
                or cell("Documents fed in", arm))
            # The cost denominator is NOT always the items figure above: on RTR-Fraud that row
            # is a request count for the internal arms. See COST_UNIT.
            unit_spec = COST_UNIT.get(tab_name, {"unit": "item"})
            denom = unit_spec.get("pin") or items_n
            mid = model_id(arm, tab)
            # The RTR-Fraud tab names no model for its incumbent column. Rather than leave the
            # run uncosted, the pricing record carries an explicit assumption, and the arm is
            # flagged so the page can say the figure rests on one.
            assumed = (pricing.get("assumed_models") or {}).get(tab_name)
            model_assumed = False
            if is_inc and assumed and mid.lower() not in rates:
                mid, model_assumed = assumed["model"], True
            rate = rates.get(mid.lower())

            # Does this arm send AUDIO into the model? The workbook says so in its own words:
            # "none - audio into the model" in the Speech to text row means no ASR stage, so
            # the audio itself is the input. Gemini prices audio input separately -- $0.50 per
            # 1M against $0.15 for text on 2.5 Flash -- so those runs are priced at the audio
            # rate, with the all-text figure kept beside them as a floor.
            stt = (cell("Speech to text", arm) or "").lower()
            sends_audio = "audio into the model" in stt

            # The 200K long-context tier is per REQUEST, not per run. Where the workbook gives
            # an item count, the mean tokens per request says whether the cheap tier can be
            # relied on. It is a mean, so it is evidence and not proof; recorded as such.
            per_request = (tin["total"] / items_n) if (tin["total"] and items_n) else None
            tier_ok = None
            if rate and rate.get("long_context_threshold_tokens") and per_request:
                tier_ok = per_request < rate["long_context_threshold_tokens"]

            # One cost per run. A run that feeds audio into the model is priced at the AUDIO
            # input rate, because the audio is what its input total is mostly made of; the
            # system prompt inside that total is text and cheaper, so the figure is an upper
            # bound and `cost_usd_floor` carries the all-text lower bound beside it.
            cost = cost_floor = None
            if rate and tin["total"] is not None and billable_out:
                out_cost = billable_out / 1e6 * rate["output_usd_per_1m"]
                in_rate = rate["input_usd_per_1m"]
                if sends_audio and rate.get("input_audio_usd_per_1m"):
                    in_rate = rate["input_audio_usd_per_1m"]
                    cost_floor = round(
                        tin["total"] / 1e6 * rate["input_usd_per_1m"] + out_cost, 4)
                cost = round(tin["total"] / 1e6 * in_rate + out_cost, 4)

            headline = (tab["derived"].get("headline") or {}).get(arm)
            columns.append({
                "use_case": tab_name,
                "arm": re.sub(r"\s+", " ", arm).strip(),
                "model": mid,
                "side": "incumbent" if is_inc else "internal",
                "headline_f1": headline,
                "items_scored": items_n,
                "items_label": items_unit or (
                    "calls" if by_label.get("Calls scored")
                    else "model calls" if by_label.get("Model calls")
                    else "items"),
                "input_tokens": tin["total"],
                "input_parts": tin["parts"],
                "output_tokens": tout["total"],
                "thinking_tokens": tout["thinking"],
                "billable_output_tokens": billable_out or None,
                "total_tokens_computed": computed or None,
                "total_tokens_published": published["total"],
                "reconciled": reconciled,
                "latency_s": seconds(cell("Per call", arm) or cell("Per page", arm)),
                # Kept verbatim: where the workbook has no number it has a REASON, and a bare
                # dash in a latency cell reads as missing data rather than as an unmeasurable
                # run. Sentiment QA's best-scoring arm says "cannot meansure" here.
                "latency_raw": (cell("Per call", arm) or cell("Per page", arm)).strip(),
                "latency_unit": "per call" if by_label.get("Per call") else "per page",
                "round_s": seconds(cell("Time for the whole round  -  seconds", arm)),
                "round_raw": cell("Time for the whole round  -  seconds", arm).strip(),
                "rate_used": ({"openrouter_slug": rate.get("openrouter_slug"),
                               "input_usd_per_1m": rate["input_usd_per_1m"],
                               "input_audio_usd_per_1m": rate.get("input_audio_usd_per_1m"),
                               "output_usd_per_1m": rate["output_usd_per_1m"],
                               "mode": rate.get("mode"),
                               "source_url": rate.get("source_url"),
                               "confidence": rate.get("confidence")} if rate else None),
                "sends_audio": sends_audio,
                "cost_unit": unit_spec["unit"],
                "cost_denominator": denom,
                "cost_denominator_pinned": "pin" in unit_spec,
                "model_assumed": model_assumed,
                "tokens_per_request": round(per_request) if per_request else None,
                "under_long_context_tier": tier_ok,
                "input_rate_applied": (rate["input_audio_usd_per_1m"] if sends_audio and rate
                                       and rate.get("input_audio_usd_per_1m")
                                       else rate["input_usd_per_1m"] if rate else None),
                "input_rate_kind": ("audio" if sends_audio and rate
                                    and rate.get("input_audio_usd_per_1m") else "text"),
                "cost_usd": cost,
                "cost_usd_floor": cost_floor,
                # NOT pre-rounded. The per-item figure is displayed at four decimals,
                # so storing an intermediate at five rounds it twice. Sentiment Retention
                # is 1.9838/97 = 0.0204515, which prints as $0.0205 -- but rounded first
                # to 0.02045, whose double sits a hair under the tie, it printed $0.0204.
                # Divide and let the display perform the only rounding. The invariant
                # cost_per_item == cost_usd / denominator still holds exactly, which is
                # what tests/test_usecase_matrix.py asserts.
                "cost_per_item_usd": (cost / denom
                                      if cost is not None and denom else None),
                # The audio/text band matters more at this grain than at the run grain: the
                # per-item figure is about to become the page's only cost number, and on the
                # sentiment tabs the floor is roughly half of it.
                "cost_per_item_usd_floor": (cost_floor / denom
                                            if cost_floor is not None and denom else None),
                "cost_status": (
                    "computed" if cost is not None
                    else "not-metered" if not is_inc
                    else "rate not established"),
            })

    bad = [c for c in columns if c["reconciled"] is False]
    return {
        "generated": GENERATED,
        "generated_from": "scripts/usecase_matrix_figures.py",
        "source_file": book["source_file"],
        "pricing_source": PRICING.name if PRICING.is_file() else None,
        "pricing_status": pricing.get("status", "established" if rates else "not established"),
        "columns": columns,
        "reconciliation": {
            "checked": sum(1 for c in columns if c["reconciled"] is not None),
            "passed": sum(1 for c in columns if c["reconciled"] is True),
            "failed": [f"{c['use_case']} / {c['arm']}" for c in bad],
            "rule": "input + output + thinking == the workbook's own 'Total tokens' row, "
                    "wherever it publishes one. An arm that fails is not costed.",
        },
        "thinking_token_note":
            "Sentiment MNP's incumbent reports 176,626 output and 227,974 thinking tokens "
            "against a published total of 1,924,125 = 1,519,525 + 176,626 + 227,974. Thinking "
            "tokens are therefore outside the printed output line and inside the total, so a "
            "cost taken from the output line alone would undercount that run's billable output "
            "by 129%.",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="usecase_matrix_figures")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    text = json.dumps(build(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        ok = current == text
        print(f"{OUT.relative_to(REPO)} "
              + ("is up to date." if ok else "IS STALE -- regenerate it."))
        return 0 if ok else 1
    OUT.write_text(text, encoding="utf-8")
    d = json.loads(text)
    r = d["reconciliation"]
    print(f"wrote {OUT.relative_to(REPO)}  ({len(d['columns'])} columns)")
    print(f"  token reconciliation: {r['passed']}/{r['checked']} exact"
          + (f"  FAILED: {r['failed']}" if r["failed"] else ""))
    costed = [c for c in d["columns"] if c["cost_usd"] is not None]
    print(f"  pricing: {d['pricing_status']};  {len(costed)} of "
          f"{sum(1 for c in d['columns'] if c['side']=='incumbent')} incumbent runs costed")
    tot = 0.0
    for c in costed:
        tot += c["cost_usd"]
        flag = "  [model ASSUMED]" if c["model_assumed"] else ""
        floor = f"  floor ${c['cost_usd_floor']:,.2f}" if c["cost_usd_floor"] else ""
        print(f"    {c['use_case']:22s} {c['model']:24s} ${c['cost_usd']:>7,.2f}"
              f"  (${c['cost_per_item_usd']:.4f}/item, {c['input_rate_kind']} input rate)"
              f"{floor}{flag}")
    print(f"    {'TOTAL':22s} {'':24s} ${tot:>7,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
