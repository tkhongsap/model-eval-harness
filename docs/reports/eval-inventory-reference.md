# Eval inventory — engineering reference

Companion to the one-page summary in [`eval-inventory.html`](./eval-inventory.html). Every
figure is marked and checked against [`eval-inventory.json`](./eval-inventory.json), which
`scripts/eval_inventory.py` reads from the repository — test-set rows from the ground-truth
CSVs, audio counts from the corpus roots, experiment status from the plan files, spend from the
run records, and the production app list from `production-reference/` itself.

```bash
PYTHONPATH=src python scripts/eval_inventory.py            # regenerate
PYTHONPATH=src python scripts/eval_inventory.py --check    # stale?
PYTHONPATH=src python scripts/doc_claims.py --check        # figures still match?
```

An inventory is exactly the document that is true the day it is written and quietly wrong three
weeks later. This one recounts itself.

---

## 1. What exists

**<!--claim:eval-inventory.json:totals.text_sets:int-->5<!--/--> text sets carrying
<!--claim:eval-inventory.json:totals.labelled_text_rows:int-->344<!--/--> labelled rows, and
<!--claim:eval-inventory.json:totals.audio_sets:int-->3<!--/--> audio corpora carrying
<!--claim:eval-inventory.json:totals.audio_calls:int-->296<!--/--> calls.** All of it is
Retention, all of it Thai, all of it synthetic.

| set | kind | calls | GT rows | role |
|---|---|---:|---:|---|
| `retention_v1` | text | <!--claim:eval-inventory.json:test_sets[0].calls:int-->20<!--/--> | <!--claim:eval-inventory.json:test_sets[0].gt_rows:int-->22<!--/--> | seed |
| `retention_v2` | text | <!--claim:eval-inventory.json:test_sets[1].calls:int-->100<!--/--> | <!--claim:eval-inventory.json:test_sets[1].gt_rows:int-->108<!--/--> | scale |
| `retention_v3` | text | <!--claim:eval-inventory.json:test_sets[2].calls:int-->138<!--/--> | <!--claim:eval-inventory.json:test_sets[2].gt_rows:int-->150<!--/--> | primary text pack |
| `retention_challenge_v1` | text | <!--claim:eval-inventory.json:test_sets[3].calls:int-->50<!--/--> | <!--claim:eval-inventory.json:test_sets[3].gt_rows:int-->64<!--/--> | adversarial |
| `block_a..d` | text | <!--claim:eval-inventory.json:test_sets[4].calls:int-->20<!--/--> | — | unit fixtures: clear, Thai, tiebreak, escape |
| `asr-eval` | audio | <!--claim:eval-inventory.json:test_sets[5].calls:int-->20<!--/--> | — | committed audio seed |
| `asr-eval-v2` | audio | <!--claim:eval-inventory.json:test_sets[6].calls:int-->138<!--/--> | <!--claim:eval-inventory.json:test_sets[6].gt_rows:int-->138<!--/--> | E23 corpus |
| `asr-eval-v3` | audio | <!--claim:eval-inventory.json:test_sets[7].calls:int-->138<!--/--> | <!--claim:eval-inventory.json:test_sets[7].gt_rows:int-->138<!--/--> | E24 corpus, product labels corrected |

Rows exceed calls on the text packs because the grain is **one row per (call, product)** — a
call naming two services contributes two rows. The audio corpora are one row per call by
construction.

**4 preregistered experiments**, all Retention:

| id | status | arms | corpus frozen |
|---|---|---:|---|
| `retention-e5` | <!--claim:eval-inventory.json:experiments[2].status:text-->locked<!--/--> | <!--claim:eval-inventory.json:experiments[2].arms:int-->3<!--/--> | n/a — text pack |
| `retention-e7` | <!--claim:eval-inventory.json:experiments[3].status:text-->draft<!--/--> | <!--claim:eval-inventory.json:experiments[3].arms:int-->3<!--/--> | n/a — text pack |
| `retention-e23` | <!--claim:eval-inventory.json:experiments[0].status:text-->draft<!--/--> | <!--claim:eval-inventory.json:experiments[0].arms:int-->2<!--/--> | **no** — 1,002 model calls ran against an unstamped plan |
| `retention-e24` | <!--claim:eval-inventory.json:experiments[1].status:text-->qualified<!--/--> | <!--claim:eval-inventory.json:experiments[1].arms:int-->5<!--/--> | **yes** — stamped before the first call |

E17, E20, E21 and E22 are written up in `docs/` but have no plan file; they predate the
preregistration discipline or were diagnostics rather than experiments.

## 2. What carries over to any new eval

App-agnostic, already built, already gated:

- `evalharness.metrics` — per-class tp/fp/fn/tn, weighted F1
- `evalharness.compare` — paired sign test at α=1/64, cluster bootstrap over **calls not rows**,
  `exact_band` returning UNDERPOWERED rather than a false tie
- `evalgen.runner` / `client` / `runtime` — the call loop, retry accounting, decoding schema,
  model-identity capture
- `evalgen.experiments` — plan validation, dispatched per experiment id
- `scripts/verify.py` — 10 gates across two interpreters
- `scripts/doc_claims.py` — published figures against their source
- `scripts/freeze_corpus.py`, `corpus_diff.py`, `corpus_fix_effect.py` — corpus provenance
- `scripts/audit_packet.py` + `audit_score.py` + `audit_review_models.py` — the blind-audit rig,
  reusable against any label space
- the preregistration discipline itself

## 3. Cost — what has actually been measured

**Retention, per call.** E24 metered
**$<!--claim:eval-inventory.json:spend.e24_incumbent_usd:f3-->3.855<!--/-->** across
<!--claim:eval-inventory.json:spend.e24_incumbent_calls:int-->414<!--/--> incumbent calls =
**$<!--claim:eval-inventory.json:spend.e24_usd_per_call:f3-->0.009<!--/--> a call**. Internal
arms are $0.00 metered — company GPU, no per-call price, which is not the same as free.

**Total recorded spend** across
<!--claim:eval-inventory.json:spend.runs_with_cost:int-->8<!--/--> runs:
**$<!--claim:eval-inventory.json:spend.metered_usd_total:f3-->18.200<!--/-->**.

**sentiment_qa token A/B**, <!--claim:eval-inventory.json:sentiment_qa_token_ab.items:int-->24<!--/-->
items, four arms. Turning reasoning off:

| | baseline | reasoning-off |
|---|---:|---:|
| median completion tokens | <!--claim:eval-inventory.json:sentiment_qa_token_ab.baseline_completion_med:f3-->9231.500<!--/--> | <!--claim:eval-inventory.json:sentiment_qa_token_ab.reasoning_off_completion_med:f3-->3461.000<!--/--> |
| valid JSON | <!--claim:eval-inventory.json:sentiment_qa_token_ab.baseline_json_ok:int-->14<!--/-->/24 | <!--claim:eval-inventory.json:sentiment_qa_token_ab.reasoning_off_json_ok:int-->22<!--/-->/24 |
| cost | $<!--claim:eval-inventory.json:sentiment_qa_token_ab.baseline_cost_usd:f3-->0.472<!--/--> | $<!--claim:eval-inventory.json:sentiment_qa_token_ab.reasoning_off_cost_usd:f3-->0.368<!--/--> |

A **<!--claim:eval-inventory.json:sentiment_qa_token_ab.completion_cut:pct1-->62.5%<!--/-->**
cut in completion tokens with *better* JSON validity.

Separately: the 30–40k figure reported from production is **input**, not output — a
94,174-character `user_config.xlsx` at ~31,400 tokens, with `cached_tokens: 0`.

> **The caveat that matters more than any number above.** We have told production that turning
> reasoning off is a win. **There is no accuracy eval behind that advice.** No labelled
> sentiment_qa batch exists; `docs/sentiment-qa-token-ask.md` is the unsent ask that would
> unblock one. Until then the recommendation rests on token count and JSON validity alone.

## 4. The gap

`production-reference/` holds
<!--claim:eval-inventory.json:production.app_directories[0]:text-->rtr-fraud-validation-main<!--/-->
and three sibling app directories, carrying
<!--claim:eval-inventory.json:production.tasks_total:int-->6<!--/--> model-driven tasks. We have
an eval for <!--claim:eval-inventory.json:production.tasks_covered:int-->1<!--/-->;
<!--claim:eval-inventory.json:production.tasks_uncovered:int-->4<!--/--> have none at all and
one has cost-only coverage.

| task | modality | status |
|---|---|---|
| Retention labelling | Thai audio → JSON | **covered** |
| MNP retention labelling | Thai audio → JSON | none — label space declared, nothing else |
| QA pipeline fact-check | audio/text → ~118 keys | cost only |
| Telesale rubric scoring | audio → weighted rubric | none |
| Tax invoice extraction | document image → fields | none |
| Shop image classification | images → 3 detections | none |

### The architectural fact underneath it

`src/evalgen/apps.py` registers
**<!--claim:eval-inventory.json:app_bindings.registered:int-->1<!--/--> app binding**. There is
**<!--claim:eval-inventory.json:app_bindings.adapters[0]:text-->retention<!--/-->** as the only
adapter and the only schema. The harness *is* parameterised — `--app`,
`binding(application_id)`, and `adapters/registry.py` resolving a loader from the hashed
contract string rather than a second table that could disagree with it — and `binding()`'s own
refusal enumerates exactly what a new app needs: *"adapter, prompt, schema, testset reference
and decision units"*.

**But it has never run a second application.** That extensibility is designed and unexercised,
and no amount of reading the code settles whether it holds.

## 5. What each candidate would cost to build

Reusable in all cases: the call loop, retries, identity capture, plan/freeze/verify, the paired
statistics, the blind-audit rig, the figure gate.

| candidate | needs building | new metric code? |
|---|---|---|
| **MNP** | adapter, schema, prompt, test set, validator branch | no — same label space + 1 reason class |
| **sentiment_qa** | all of the above **+ labelled data that does not exist** | no — key-level accuracy |
| **Tax invoice** | adapter, schema, prompt, test set, validator branch | no — per-field exact match, thresholds already written |
| **RTR fraud** | all of the above + image handling in the runner | yes — counting/denominator checks |
| **Telesale** | all of the above | **yes** — additive negative points with per-section caps is not classification |

Files a builder touches for a new app, by pattern:
`src/evalharness/adapters/<app>.py`, `src/evalgen/schemas/<app>.json`,
`src/evalgen/prompts/<app>_*.txt` (+ `manifest.json`), a `BINDINGS` entry in
`src/evalgen/apps.py`, a validator branch in `src/evalgen/experiments.py`, a test set under
`tests/fixtures/testsets/`, and a plan under `experiments/`.

## 6. Recommendation

1. **MNP first** — the cheapest possible test of the extensibility claim. Same modality, same
   scorer, label space differs by one reason class. If the harness is really multi-app this is
   short work; if it is not, this is where we find out cheaply rather than three apps later.
2. **sentiment_qa accuracy second** — the only place we have advised production without
   evidence. Blocked on data; unblock it before the reasoning-off change ships anywhere.
3. **Tax invoice third** — different modality, but the easiest eval shape here and the
   thresholds are already specified.
4. **RTR fraud fourth** — highest build cost, most interesting failure surface. The prompt
   requires the model to count its inputs, which is a known weak spot.
5. **Telesale last** — needs genuinely new metric code. Do it once extensibility is settled.

---

`RECONCILED: NO`. Every number here comes from synthetic corpora. No production call has been
through any part of this harness.
