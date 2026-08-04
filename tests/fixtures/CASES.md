# Synthetic fixture: what each case exercises

**Grain is `(call_id, phone_number, product)`, not call.** `prepare_predictions` emits one row
per product (`fact_checker.py:603-631`) and the scorer merges on
`['call_id','phone_number','product']` (`:1075`), while the product dimension uses a separate
`groupby(['call_id','phone_number'])` frame (`:1080-1081`). Three dimensions, three denominators.

**Label spaces, verbatim from production** (do not edit without re-reading the source):

- `call_result` (`:791`): `save`, `churn`, `unknown`, `undefined`
- `reason` (`:857-869`): `network`, `promotion related`, `device promotion related`, `save cost`,
  `contract end`, `sale upsell problem`, `dissatisfied service`, `other`, `post to pre`,
  `customer reason`, `down sell not success`
- `product` (`:971`): `postpaid`, `tol`, `tvs`, `unknown`

No real customer data. All `call_id` values are `5xxx`, all phone numbers are `08100000xx`.

---

## Cases

| # | call_id | What it exercises | Why it matters |
|---|---|---|---|
| C1 | 5001 | Exact match on all three dimensions | The sanity anchor. If this fails nothing else means anything. |
| C2 | 5002 | `call_result` wrong, one reason missed | The ordinary error path: produces FP on one class and FN on another. |
| C3 | 5003 | Case and whitespace drift (`"Save"` vs `"save "`, `"Network"` vs `"network"`) | `pre_process` lowercases/strips only when `dtype == 'object'` (`:734,:738`), which is **False on pandas 3.x**. Verified: this case alone flips `call_result` accuracy from 0.75 to 0.25 and zeroes all weights. The reason dimension survives because `get_reasons_set` lowercases on its own line (`:877`). This case is the version pin's regression test. |
| C4 | 5004 | Ground truth present, **no prediction row** | The outer merge (`:1074-1078`) must score this as FN, not drop it. Contrast with Sentiment QA, which inner-merges and would silently delete it. |
| C5 | 5005 | `phone_number` is `0` in GT, `"0"` in the prediction | Production maps both to `None` (`:718-730`). If the harness normalises differently the join key diverges and this reads as a false regression. |
| C6 | 5006 | One call, **two products** (`postpaid` + `tol`), two rows | The merge is three-key. A single-product fixture never tests the real join, and the product dimension collapses these two rows back to one call. |
| C7 | 5007 | Multi-label reason cell: `main = "network, save cost"` | `get_reasons_set` comma-splits each cell and unions main/secondary/third into a **set** (`:871-879`), so rank is discarded. A positional main-vs-main comparison is wrong. |
| C8 | 5008 | **Parse failure**: `parse_ok = false`, `product` empty | The defect this harness deliberately does not inherit. In production the row becomes a phantom, `dropna` (`:756`) deletes it from `call_result`, and `reason_calculation` credits it 11 TNs (`:899-902`). |
| C9 | 5009 | **Extra prediction**, no matching GT row | Must not crash, and must not be silently credited. |
| C10 | 5010 | Perfectly wrong on every dimension | The lower anchor. Accuracy 0.0 must be reachable, not just approachable. |

## Arms

| File | Purpose |
|---|---|
| `retention_gt.csv` | The human labels. The answer key for both arms. |
| `retention_arm_incumbent.csv` | Stands in for Gemini. Gets C1-C7 mostly right, C10 wrong. |
| `retention_arm_candidate.csv` | Stands in for the self-hosted model. Same items, different errors, plus the C8 parse failure, so the paired comparison and the 2x2 disagreement table have something real to report. |
| `retention_arm_empty.csv` | **The degenerate arm.** Every prediction blank, `parse_ok = false`. Production scores this **0.909 weighted accuracy on reasons with recall 0.0**, clearing both the `acceptable: 85` and `good: 90` bands (`retention.yml:76-79`). The harness must report FAIL. This is the single most important test in the pack: it is the exact shape of a vLLM arm without forced tool calling. |

## Rule

`retention_expected.csv` is written **by hand, before any metric code exists**, and holds exact
integer TP/FP/FN/TN per `(dimension, label)`.

When a test fails, the first question is whether the **fixture** is wrong, not the code. Editing
an expectation to match the output destroys the entire value of this pack. If production and the
hand computation genuinely disagree, that is either a defect to record in the goal's closed
deviation list or a misunderstanding to fix, and it gets written down either way.
