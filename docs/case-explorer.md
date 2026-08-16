# The case explorer

**What it is:** a single-page browser tool showing all 138 `retention_v3` cases — the Thai
transcript that went in, the ground truth with the evidence that justifies it, and what
all four models answered, each field marked right or wrong.

**Why it exists:** the [comparison report](./reports/model-comparison.html) gives three
weighted-F1 numbers per model. A reader who wants to know *why* Gemini scores 0.838 on
reason has nowhere to look — the number is an aggregate over 157 rows and eleven
one-vs-rest classes, and no single case explains it. This is the drill-down, and the thing
to walk a room through.

```
PYTHONPATH=src python scripts/case_explorer.py
    ->  out/case-explorer.html            retention_v3, 138 cases, 4 models  (~2 MB)

PYTHONPATH=src python scripts/case_explorer.py --config configs/comparison/retention-challenge-v1.json
    ->  out/case-explorer-challenge.html  retention_challenge_v1, 50 cases, 3 models
```

Opens straight off disk. No server, no network calls, no external references.

**Which pack, which models and which runs are a config**, not a code change:
`configs/comparison/<pack>.json` is the same file `model_comparison_report.py` reads, so the
report and the dashboard are two views of one declared comparison and cannot drift apart.
A pack without a tune/holdout split simply gets no Split filter — `retention_challenge_v1`
was authored as a single block, so there is nothing to keep apart and, unlike v3's holdout,
no contamination caveat to carry.

## Why it is not committed

It embeds **raw model completions and full transcripts**. `.gitignore` blocks those by
default at `out/` with "deliberately NO negation exceptions", and CLAUDE.md says never to
commit them. Generating it into `out/` means that control is untouched — no exception, no
hole. The committed half is the generator, the same split already used by
`model_comparison_report.py`.

It regenerates in about a minute from the run directories, so nothing is lost by not
storing it. What *is* irreplaceable is the run directories themselves — see
[RUNS.md](../RUNS.md).

## What it shows

| Section | Contents |
|---|---|
| What went in | The transcript, turn by turn, agent and customer distinguished. The 12 `long_context` cases (up to 15,590 chars) render collapsed. |
| The right answer, and why | Products x outcome x reasons, each label carrying its verbatim span **and** its `prompt.py:4390`-style citation |
| Why this case is in the set | `mechanism`, `why_it_matters`, and the `expected_failure` the case was built to provoke |
| What each model answered | Reason diffs — matched, invented, missed. Unstable cases expand to show what all three repeats said. Unscored output (`recommendation`, `keyword`, `call_event_detection`) sits behind a disclosure, labelled as not scored. |

**Slices:** family, tune/holdout, who got it right, a single model, instability,
multi-product, and free text. Filter the list and **the F1 table recomputes for that
slice**. Arrow keys or `j`/`k` step through cases.

## How the live slicing is kept honest

Filtering needs scoring in the browser, and a JavaScript reimplementation of
`evalharness.metrics` would be a **second scorer** — free to disagree with the first and be
believed anyway.

So the browser never scores. Python precomputes the *scoring atoms* — the
`(ground truth, prediction)` pairs at each dimension's own grain — and JavaScript only sums
them. The three grains are carried separately because `metrics.py` opens by calling three
different denominators "the single most important fact in the file".

Two gates:

- **Build time.** Re-aggregating the atoms in Python must equal what the real scorer
  returns, 4 models x 3 dimensions, to floating-point equality. The build fails otherwise.
- **Load time.** The page checks its own unfiltered arithmetic against the authoritative
  figures and shows a red banner if they diverge, so a later edit to the JavaScript cannot
  quietly produce wrong slice numbers.

It also refuses to build if the four runs do not share one contract, if the shape is not
138 items / 150 ground-truth rows / 414 rows per run, or if per-dimension correctness
disagrees with `report.py`'s `_item_is_correct` on any of the 552 case-model pairs.

## Two things to know when showing it

**"Correct" in the case list is whole-answer.** Every product, outcome and reason must
match, on every row of the call. That is stricter than F1, which scores one label at a
time — so a case marked wrong can still be mostly right, and a model can lose a case over
a single extra reason. The page says this above the model cards, because it otherwise
reads as a contradiction.

**Qwen3.6 has more perfect cases than Qwen3.8** — 78 against 73 correct on all three
repeats — while scoring lower on F1. Whole-item strictness and per-label F1 rank them
differently; both numbers are real.

## The dilation demo

`RET-01 -> RET-101 -> RET-102` is the same call at **1,174 / 3,524 / 11,754** characters,
one click apart in the header. Six such chains exist. They are the only place in the pack
where transcript length is the *only* thing that changed, which makes them the cleanest
way to show what long context does to each model.
