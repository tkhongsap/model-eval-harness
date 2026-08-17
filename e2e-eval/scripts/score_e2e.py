"""Score every end-to-end arm on the three retention dimensions, and decompose the gap.

Scoring is `evalharness.metrics` UNMODIFIED -- three dimensions, three denominators. That
is deliberate and it is the reason these numbers can be compared with Experiment 7's: a new
scorer would produce a new scale, and a delta across two scales is not a delta.

Three outputs, in increasing order of what they let you decide:

1. PER-ARM SCORE. What each pipeline gets right. Answers "how good is it".

2. THE DECOMPOSITION. `candidate - open ceiling` is the damage the ASR step does; the
   ceiling arm is the same text model on the same items reading a perfect transcript, so
   the ASR step is the only thing that differs. Answers "is it the ears or the brain".

3. PER-ITEM FAULT ATTRIBUTION. For every wrong answer, was the same answer also wrong when
   the model read the perfect transcript?
       ASR-caused  wrong on ASR text, right on reference  -> better ASR fixes this
       QA-caused   wrong on both                          -> better text model fixes this
       recovered   right on ASR text, wrong on reference  -> noise; report, do not celebrate
   Answers "where should the next week of work go", which a single end-to-end score cannot.

Plus EVIDENCE SURVIVAL: `retention_v3` names the exact span each label rests on. If the
span justifying `save` did not survive the ASR, a wrong `call_result` is explained rather
than merely observed.
"""
from __future__ import annotations

import json
import pathlib
import sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(REPO / "src"))

from evalgen.flatten import to_rows                        # noqa: E402
from evalgen.testsets import TestItem                      # noqa: E402
from evalharness.labelspaces import RETENTION              # noqa: E402
from evalharness.metrics import (                          # noqa: E402
    score_call_result, score_product, score_reason,
)
from evalharness.records import from_row                   # noqa: E402

PACK = ROOT / "pilot.jsonl"
HYP = ROOT / "hypotheses"
ASR = ROOT / "asr"

ARMS = ["gemini-audio", "gemini-text", "qwen-text", "qwenasr-qwen", "whisper-qwen"]
CEILING_OF = {"qwenasr-qwen": "qwen-text", "whisper-qwen": "qwen-text",
              "gemini-audio": "gemini-text"}


def load_pack() -> list[dict]:
    return [json.loads(l) for l in PACK.read_text(encoding="utf-8").splitlines()]


def as_item(rec: dict) -> TestItem:
    return TestItem(
        item_id=rec["item_id"], call_id=rec["call_id"], family=rec["family"],
        transcript_th=rec["transcript_th"], phone_number=rec.get("phone_number"),
        mechanism=rec.get("mechanism", ""), why_it_matters=rec.get("why_it_matters", ""),
        gt=tuple(rec["gt"]), evidence=rec.get("evidence", {}), rules=rec.get("rules", {}),
        expected_failure=rec.get("expected_failure", ""),
    )


def gt_records(pack: list[dict]):
    out = []
    for rec in pack:
        for g in rec["gt"]:
            out.append(from_row({
                "call_id": rec["call_id"], "phone_number": rec["phone_number"],
                "product": g.get("product"), "call_result": g.get("call_result"),
                "main": g.get("main", ""), "secondary": g.get("secondary", ""),
                "third": g.get("third", ""),
            }))
    return out


def pred_records(pack: list[dict], arm: str):
    """Flatten each arm's QA JSON to one row per product, then normalise."""
    out, per_item = [], {}
    for rec in pack:
        p = HYP / arm / f"{rec['item_id']}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        rows = to_rows(d.get("payload"), as_item(rec), parse_ok=bool(d.get("parse_ok")))
        recs = [from_row({"call_id": rec["call_id"],
                          "phone_number": rec["phone_number"], **r}) for r in rows]
        out.extend(recs)
        per_item[rec["item_id"]] = recs
    return out, per_item


def score_arm(gt, pred):
    return {
        "call_result": score_call_result(gt, pred, RETENTION.call_result),
        "reason": score_reason(gt, pred, RETENTION.reason),
        "product": score_product(gt, pred, RETENTION.product),
    }


def f1_of(res) -> float:
    return res.weighted("f1")


# --------------------------------------------------------------------- per-item correctness
def item_correct(pack: list[dict], per_item: dict, dim: str) -> dict[str, bool]:
    """Did this arm get this item's dimension exactly right? Set equality, per call."""
    truth: dict[str, set] = {}
    for rec in pack:
        if dim == "call_result":
            truth[rec["item_id"]] = {str(g.get("call_result", "")).lower().strip()
                                     for g in rec["gt"]}
        elif dim == "product":
            truth[rec["item_id"]] = {str(g.get("product", "")).lower().strip()
                                     for g in rec["gt"]}
        else:
            s = set()
            for g in rec["gt"]:
                for col in ("main", "secondary", "third"):
                    for part in str(g.get(col, "")).split(","):
                        if part.strip():
                            s.add(part.strip().lower())
            truth[rec["item_id"]] = s

    out = {}
    for item_id, recs in per_item.items():
        if dim == "call_result":
            got = {r.call_result for r in recs if r.call_result}
        elif dim == "product":
            got = {r.product for r in recs if r.product}
        else:
            got = set().union(*[set(r.reasons) for r in recs]) if recs else set()
        out[item_id] = (got == truth.get(item_id, set()))
    return out


def evidence_survival(pack: list[dict], asr_arm: str) -> dict:
    """Did the span each label rests on survive this ASR arm's transcript?"""
    d = ASR / asr_arm
    if not d.exists():
        return {}
    per_label, per_item = Counter(), {}
    for rec in pack:
        p = d / f"{rec['item_id']}.txt"
        if not p.exists():
            continue
        hyp = p.read_text(encoding="utf-8")
        hyp_ns = hyp.replace(" ", "")
        survived, total = 0, 0
        for tag, span in (rec.get("evidence") or {}).items():
            if not span:
                continue
            total += 1
            ok = span in hyp or span.replace(" ", "") in hyp_ns
            survived += ok
            per_label[(tag.split(":")[0], ok)] += 1
        per_item[rec["item_id"]] = (survived, total)
    tot = sum(t for _, t in per_item.values())
    sur = sum(s for s, _ in per_item.values())
    return {"items": per_item, "survived": sur, "total": tot,
            "rate": (sur / tot) if tot else 0.0}


def main() -> int:
    pack = load_pack()
    gt = gt_records(pack)
    present = [a for a in ARMS if (HYP / a).exists() and any((HYP / a).glob("*.json"))]
    if not present:
        print("no arm results found")
        return 1

    scored, items_by_arm, parse_stats = {}, {}, {}
    for arm in present:
        pred, per_item = pred_records(pack, arm)
        scored[arm] = score_arm(gt, pred)
        items_by_arm[arm] = per_item
        run = HYP / arm / "_run.json"
        if run.exists():
            r = json.loads(run.read_text(encoding="utf-8"))
            parse_stats[arm] = (r.get("parse_ok", 0), r.get("not_ok", 0))

    W = 100
    print("=" * W)
    print("END-TO-END RETENTION QA — audio in, QA JSON out".center(W))
    print(f"{len(pack)} calls from retention_v3, rendered to 8 kHz telephony audio".center(W))
    print("=" * W)

    print("\n\n### 1. PER-ARM SCORE  (weighted F1, higher is better)\n")
    print(f"{'Arm':16s} {'call_result':>12s} {'reason':>10s} {'product':>10s} "
          f"{'mean':>8s} {'parse_ok':>10s}")
    print("-" * W)
    for arm in present:
        s = scored[arm]
        f = [f1_of(s['call_result']), f1_of(s['reason']), f1_of(s['product'])]
        po = parse_stats.get(arm, (0, 0))
        print(f"{arm:16s} {f[0]:12.3f} {f[1]:10.3f} {f[2]:10.3f} "
              f"{sum(f)/3:8.3f} {po[0]:6d}/{po[0]+po[1]:<4d}")
    print("-" * W)
    print("Denominators differ by dimension, mirroring production exactly "
          "(metrics.py: 3 dimensions, 3 denominators).")

    print("\n\n### 2. THE DECOMPOSITION  (what the ASR step costs)\n")
    print(f"{'Candidate':16s} {'vs ceiling':16s} {'call_result':>12s} {'reason':>10s} "
          f"{'product':>10s} {'mean':>8s}")
    print("-" * W)
    for arm, ceil in CEILING_OF.items():
        if arm not in scored or ceil not in scored:
            continue
        a, b = scored[arm], scored[ceil]
        d = [f1_of(a[k]) - f1_of(b[k]) for k in ("call_result", "reason", "product")]
        print(f"{arm:16s} {ceil:16s} {d[0]:+12.3f} {d[1]:+10.3f} {d[2]:+10.3f} "
              f"{sum(d)/3:+8.3f}")
    print("-" * W)
    print("Negative = the pipeline lost this much by hearing the call instead of reading it.")

    print("\n\n### 3. FAULT ATTRIBUTION  (per call, per dimension)\n")
    for dim in ("call_result", "reason", "product"):
        print(f"  {dim}:")
        for arm, ceil in CEILING_OF.items():
            if arm not in items_by_arm or ceil not in items_by_arm:
                continue
            ca = item_correct(pack, items_by_arm[arm], dim)
            cb = item_correct(pack, items_by_arm[ceil], dim)
            buckets = Counter()
            for iid in ca:
                if iid not in cb:
                    continue
                if ca[iid] and cb[iid]:
                    buckets["both right"] += 1
                elif not ca[iid] and cb[iid]:
                    buckets["ASR-caused"] += 1
                elif not ca[iid] and not cb[iid]:
                    buckets["QA-caused"] += 1
                else:
                    buckets["recovered"] += 1
            tot = sum(buckets.values())
            print(f"    {arm:16s} n={tot:3d}  " + "  ".join(
                f"{k}={buckets[k]}" for k in
                ("both right", "ASR-caused", "QA-caused", "recovered")))
        print()

    print("\n### 4. EVIDENCE-SPAN SURVIVAL  (did the words the label rests on survive?)\n")
    for asr_arm in sorted({p.name for p in ASR.glob("*")} if ASR.exists() else set()):
        ev = evidence_survival(pack, asr_arm)
        if ev:
            print(f"  {asr_arm:24s} {ev['survived']:4d}/{ev['total']:<4d} spans survived "
                  f"({ev['rate']*100:5.1f}%)")
    print("\n  A label that flipped while its evidence span was destroyed is explained.")
    print("  A label that flipped while its span survived intact is the QA model's doing.")

    out = ROOT / "reports" / "e2e-summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "n_calls": len(pack),
        "arms": {a: {k: {"weighted_f1": f1_of(v), "denominator": v.denominator,
                         "coverage": {"items_scored": v.coverage.items_scored,
                                      "parse_failures": v.coverage.parse_failures}}
                     for k, v in scored[a].items()} for a in present},
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
