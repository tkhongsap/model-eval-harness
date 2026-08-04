# Hand-computed expectations: the working

**Written before any metric code exists.** The numbers in `retention_expected.csv` come from
here. If a test fails, read this first: the disagreement is either a mistake in this working or
a real difference in production behaviour, and both get written down rather than papered over.

## The formula, confirmed against production

`call_result_calculation` (`fact_checker.py:747-847`) builds a crosstab of
`call_result_gt` x `call_result_pred`, reindexes it to cover all four target classes, then per class:

```
tp     = cm[cls, cls]
fp     = column_sum(cls) - tp
fn     = row_sum(cls)    - tp
tn     = total_samples   - tp - fp - fn
weight = row_sum(cls)                       # ground-truth count for that class
accuracy(cls) = (tp + tn) / total_samples
weighted_avg  = sum(accuracy * weight) / sum(weight)
```

Verified against a live probe run of the real production function on a 4-row frame: it returned
`save TP=2 FP=1 FN=0 TN=1 acc=0.75 w=2`, and the formula above reproduces every cell.

Two behaviours this encodes, both confirmed empirically rather than assumed:

- **A missing prediction is retained.** `dropna(subset=['call_result_gt'])` (`:756`) removes rows
  with no *ground truth*, not rows with no *prediction*. The outer merge leaves `call_result_pred`
  as NaN, which forms its own crosstab column, so the row still counts toward `total_samples` and
  the class's `row_sum`. It scores FN. (Probe: `unknown` had FN=1, weight=1, total=4.)
- **A class with weight 0 contributes nothing to the weighted average**, even when its own
  accuracy reads 1.00. Production reports that 1.00, which is misleading in isolation.

---

## Merge result

Outer merge on `['call_id', 'phone_number', 'product']`. GT has 10 rows; the incumbent arm has 10
rows but is missing `5004` and adds `5009`. So the merged frame has **11 rows**, of which
`call_result_gt` is NaN for exactly one (`5009`, the orphan prediction).

`call_result_df` after `dropna(subset=['call_result_gt'])` = **10 rows**. `total_samples = 10`.

## Dimension 1: call_result, incumbent arm

Ground-truth class counts across the 10 scored rows:

| class | rows | count |
|---|---|---|
| `save` | 5001, 5003, 5005, 5006/postpaid, 5007 | 5 |
| `churn` | 5002, 5006/tol, 5008 | 3 |
| `unknown` | 5004 | 1 |
| `undefined` | 5010 | 1 |

Per-row outcome:

| row | gt | pred | note |
|---|---|---|---|
| 5001 postpaid | save | save | correct |
| 5002 postpaid | churn | save | C2, wrong |
| 5003 tol | save | save | C3, only correct once case/space is normalised |
| 5004 postpaid | unknown | NaN | C4, no prediction |
| 5005 tvs | save | save | C5, joins only if `0` and `"0"` both normalise to None |
| 5006 postpaid | save | save | correct |
| 5006 tol | churn | churn | correct |
| 5007 postpaid | save | save | correct |
| 5008 postpaid | churn | churn | correct |
| 5010 postpaid | undefined | save | C10, wrong |

Crosstab (rows = ground truth, columns = prediction):

| gt \ pred | save | churn | NaN |
|---|---|---|---|
| **save** | 5 | 0 | 0 |
| **churn** | 1 | 2 | 0 |
| **unknown** | 0 | 0 | 1 |
| **undefined** | 1 | 0 | 0 |

Column sums: `save` = 5+1+0+1 = **7**, `churn` = **2**, `NaN` = **1**. Grand total = **10**.

Per class:

| class | tp | fp | fn | tn | weight | accuracy |
|---|---|---|---|---|---|---|
| `save` | 5 | 7-5 = **2** | 5-5 = **0** | 10-5-2-0 = **3** | 5 | (5+3)/10 = **0.80** |
| `churn` | 2 | 2-2 = **0** | 3-2 = **1** | 10-2-0-1 = **7** | 3 | (2+7)/10 = **0.90** |
| `unknown` | 0 | 0-0 = **0** | 1-0 = **1** | 10-0-0-1 = **9** | 1 | (0+9)/10 = **0.90** |
| `undefined` | 0 | 0-0 = **0** | 1-0 = **1** | 10-0-0-1 = **9** | 1 | (0+9)/10 = **0.90** |
| **total** | **7** | **2** | **3** | **28** | **10** | weighted **0.85** |

Weighted average: (0.80x5 + 0.90x3 + 0.90x1 + 0.90x1) / 10 = (4.0 + 2.7 + 0.9 + 0.9) / 10 = **0.85**

### The C3 regression check

If the fixture is run under pandas 3.x, `pre_process`'s `dtype == 'object'` guard is False, the
lowercase/strip never fires, and GT `"Save"` no longer matches prediction `"save "`. Row 5003
leaves the `save` diagonal, `save` tp drops 5 -> 4, and a spurious `"Save"` class appears in the
index. The `call_result` block above is therefore **also the version-pin test**: if these numbers
do not reproduce, check `pip freeze` before touching the harness.

## Dimension 2: reason, incumbent arm

**The denominator is different, and this is not a mistake.** `reason_calculation` (`:849-962`)
never calls `dropna`. It runs one-vs-rest over the **full merged frame, all 11 rows**, including
the orphan prediction `5009` that `call_result` excluded. So `total = 11` here and `total = 10`
there. Any harness that reuses one denominator across dimensions is wrong.

Per class: `tp/fp/fn/tn` are set-membership counts over the 11 rows, `weight = tp + fn` (the
ground-truth positive count), and `accuracy = (tp + tn) / 11`.

Row-level reason sets after `get_reasons_set` (comma-split, stripped, lowercased, unioned):

| row | key | gt set | pred set |
|---|---|---|---|
| 1 | 5001 postpaid | {network} | {network} |
| 2 | 5002 postpaid | {save cost, network} | {save cost} |
| 3 | 5003 tol | {network} | {network} |
| 4 | 5004 postpaid | {contract end} | {} (no prediction) |
| 5 | 5005 tvs | {other} | {other} |
| 6 | 5006 postpaid | {network} | {network} |
| 7 | 5006 tol | {save cost} | {save cost} |
| 8 | 5007 postpaid | {network, save cost} | {save cost, network} |
| 9 | 5008 postpaid | {promotion related} | {promotion related} |
| 10 | 5009 postpaid | {} (no ground truth) | {network} |
| 11 | 5010 postpaid | {other} | {network} |

Note row 8: the ground truth cell is the single string `"network, save cost"` and the prediction
is `"save cost, network"`. After comma-split and set-union these are **equal**, which is the point
of case C7: rank is discarded, so a positional main-vs-main comparison would wrongly score this a miss.

| # | class | tp | fp | fn | tn | weight | accuracy | precision | recall | f1 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | network | 4 | 2 | 1 | 4 | 5 | 0.7273 | 0.6667 | 0.8000 | 0.7273 |
| 2 | promotion related | 1 | 0 | 0 | 10 | 1 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| 3 | device promotion related | 0 | 0 | 0 | 11 | 0 | 1.0000 | 0 | 0 | 0 |
| 4 | save cost | 3 | 0 | 0 | 8 | 3 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| 5 | contract end | 0 | 0 | 1 | 10 | 1 | 0.9091 | 0 | 0 | 0 |
| 6 | sale upsell problem | 0 | 0 | 0 | 11 | 0 | 1.0000 | 0 | 0 | 0 |
| 7 | dissatisfied service | 0 | 0 | 0 | 11 | 0 | 1.0000 | 0 | 0 | 0 |
| 8 | other | 1 | 0 | 1 | 9 | 2 | 0.9091 | 1.0000 | 0.5000 | 0.6667 |
| 9 | post to pre | 0 | 0 | 0 | 11 | 0 | 1.0000 | 0 | 0 | 0 |
| 10 | customer reason | 0 | 0 | 0 | 11 | 0 | 1.0000 | 0 | 0 | 0 |
| 11 | down sell not success | 0 | 0 | 0 | 11 | 0 | 1.0000 | 0 | 0 | 0 |
| | **weighted_avg** | **9** | **2** | **3** | **107** | **12** | **0.8636** | **0.7778** | **0.7500** | **0.7475** |

Working for the two non-trivial rows:

- **network**: in ground truth on rows 1, 2, 3, 6, 8 (five, including 5002 where it is the
  *secondary* reason). In predictions on rows 1, 3, 6, 8, 10, 11 (six). So tp = {1,3,6,8} = 4,
  fp = {10, 11} = 2, fn = {2} = 1, tn = 11-4-2-1 = 4 (rows 4, 5, 7, 9). weight = 4+1 = 5.
- **other**: ground truth rows 5 and 11; predicted only on row 5. tp = 1, fn = 1 (row 11, where
  the model said `network` instead), fp = 0, tn = 9. weight = 2.

Weighted average: sum(accuracy x weight) / 12
= (0.7273x5 + 1.0x1 + 1.0x3 + 0.9091x1 + 0.9091x2) / 12
= (3.6364 + 1.0 + 3.0 + 0.9091 + 1.8182) / 12 = 10.3636 / 12 = **0.8636**

### Where the 0.909 degenerate result comes from

Six of the eleven classes never appear in this fixture at all, and each scores `accuracy = 1.0000`
with `weight = 0`. They contribute nothing to the weighted average here **because there is real
signal to weight against**. Strip the predictions out entirely (`retention_arm_empty.csv`) and
every class collapses to `tp = 0, fp = 0, fn = (gt count), tn = (rows - gt count)`: accuracy stays
high because true negatives dominate, while recall goes to 0. That is the whole mechanism behind
the 0.909 figure, and it is why **recall and F1 carry the gate, not accuracy**.

## Dimension 3: product, incumbent arm

Computed on `merged2_df`: `groupby(['call_id','phone_number'])` with `product` collapsed to a set
(`:1080-1081`), then outer-merged. Denominator is **calls, not product-rows**, so it differs again
from both dimensions above. Three dimensions, three denominators: 10, 11, and 9.

### Defect found by case C5: calls with no phone number silently vanish from this dimension

`groupby(['call_id','phone_number'])` is called **without `dropna=False`**, and pandas drops NaN
group keys by default. `pre_process` maps `phone_number` values of `0` and `'0'` to `None`
(`:718-730`). So **any call whose phone number is zero or blank is dropped from the product
dimension entirely**, while still being scored in `call_result` and `reason`.

Verified directly:

```
df  = [('5005', None, 'tvs'), ('5001', '0810000001', 'postpaid')]
df.groupby(['call_id','phone_number'])['product'].apply(set).reset_index()
-> 1 row. 5005 is gone.
```

Consequence in this fixture: call 5005 (`phone_number = 0`, product `tvs`) disappears from both
sides. `tvs` therefore scores `weight = 0` and `accuracy = 1.0000`, which reads as a perfect score
for a class that was **never evaluated**. In production this silently shrinks the product sample by
however many calls carry a null phone number, and nothing in the report says so.

**Harness behaviour:** reproduce the drop (so the differential test agrees), but count it. The
dropped rows appear in this dimension's `items_requested` minus `items_joined`, so the coverage
block makes the loss visible instead of silent. **Add to the data contract:** ask how many
ground-truth rows carry a null, blank, or zero `phone_number`, because that number is the size of
this blind spot.

### Groups after the drop

Ground truth: 5001 {postpaid}, 5002 {postpaid}, 5003 {tol}, 5004 {postpaid}, 5006 {postpaid, tol},
5007 {postpaid}, 5008 {postpaid}, 5010 {postpaid}. **8 groups** (5005 dropped).

Incumbent: 5001, 5002, 5003, 5006 {postpaid, tol}, 5007, 5008, 5009 {postpaid}, 5010. **8 groups.**

Outer merge on `['call_id','phone_number']` gives **9 rows**: the eight shared keys plus 5004
(ground truth only, prediction set empty) and 5009 (prediction only, ground truth set empty),
minus 5005 which is absent from both. `total = 9`.

| class | tp | fp | fn | tn | weight | accuracy | precision | recall | f1 |
|---|---|---|---|---|---|---|---|---|---|
| postpaid | 6 | 1 | 1 | 1 | 7 | 0.7778 | 0.8571 | 0.8571 | 0.8571 |
| tol | 2 | 0 | 0 | 7 | 2 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| tvs | 0 | 0 | 0 | 9 | 0 | 1.0000 | 0 | 0 | 0 |
| unknown | 0 | 0 | 0 | 9 | 0 | 1.0000 | 0 | 0 | 0 |
| **weighted_avg** | **8** | **1** | **1** | **26** | **9** | **0.8272** | **0.8889** | **0.8889** | **0.8889** |

Working for `postpaid`: in ground truth on 5001, 5002, 5004, 5006, 5007, 5008, 5010 (seven); in
predictions on 5001, 5002, 5006, 5007, 5008, 5009, 5010 (seven). tp = the six shared, fp = 5009
(predicted with no ground truth), fn = 5004 (ground truth with no prediction), tn = 1 (5003, which
is `tol` only). weight = 6 + 1 = 7.

Weighted average: (0.7778x7 + 1.0x2) / 9 = (5.4444 + 2.0) / 9 = **0.8272**

Note `tvs` at `accuracy = 1.0000, weight = 0`: that is the vanished case C5, and it is exactly the
shape of a class that looks perfect because it was never tested.

---

## Summary: the three denominators

| dimension | frame | rows | why it differs |
|---|---|---|---|
| `call_result` | merged, after `dropna(subset=['call_result_gt'])` | **10** | orphan prediction 5009 removed |
| `reason` | merged, no dropna | **11** | orphan prediction retained |
| `product` | call-grain groupby, NaN keys dropped | **9** | orphan retained, but 5005 lost to the null phone number |

A harness that computes one denominator and reuses it produces three wrong numbers.
