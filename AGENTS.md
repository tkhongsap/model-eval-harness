# Project Context for AI Agents

**Last updated:** 2026-08-08

## Mission

> Score model outputs against human labels so True can decide, on evidence, whether a
> self-hosted model is good enough to replace Gemini in four production call-centre
> applications.

~~*"It does not run models: the app team runs both arms, this scores what they produce."*~~
**CORRECTED. That sentence was true until `src/evalgen/` landed.** Real call data has
still not arrived (`adapters/retention.py` remains the file that changes when it does),
and the repository now runs both arms itself, against a synthetic Thai testset, and
scores what it produced. The mission above is unchanged by that: what moved is where the
scorers' input comes from, not what they do with it, and the scoring half still makes no
model calls.

## Architecture

**Two packages, and the boundary between them is the load-bearing part.**
`src/evalharness/` scores; `src/evalgen/` produces the outputs it scores, by calling
models through a declared OpenAI-compatible runtime. OpenRouter is the default hosted
runtime; an internal company GPU is represented by a reviewed runtime manifest.
`evalharness` imports no model client, and
`tests/test_boundary.py` enforces that by parsing its imports rather than by trusting
this sentence: the claim is a property of what the code imports, so it stops being true
the moment somebody adds `import openai` for a plausible-sounding reason.

- **Language**: Python 3.12, standard library only for the scoring path
- **Backend**: none. Two libraries plus a test suite, not a service. No HTTP surface, no
  database, no deployment target. `evalgen` makes outbound calls; nothing serves anything.
- **AI/ML**: provider-neutral OpenAI-compatible generation in `src/evalgen/` only.
  **The scoring library still makes no model calls, deliberately.** `client.py` is the
  one file under `src/` that imports `openai`; `runtime.py` owns non-secret endpoint,
  request-dialect and build identity. The SDK stays in `src/evalgen/requirements.txt`
  rather than the scoring pins. `scripts/openrouter-smoketest/` remains exploratory.
- **Auth**: no users or sessions. `EVAL_HARNESS_KEY_HMAC` salts item keys. Generation
  credentials come from the environment variable named by the selected runtime manifest;
  neither value may be written to Git, manifests, logs or reports.
- **External integrations**: the declared model runtime at generation time, none at
  scoring time. OpenRouter is the default hosted route; internal GPU endpoints use the
  same client boundary. The
  test suite optionally reaches True's production source tree via `TRUE_SOURCE_ROOT` to
  run a differential check.
- **Infrastructure**: a venv pinned to production's own dependency versions, plus
  `openai` wherever generation is run.

## Key Files

- `src/evalharness/metrics.py`: the three scorers. **Three dimensions, three
  denominators** is the most important fact in the repository.
- `src/evalharness/records.py`: the normalized record and every normalisation rule,
  each citing the production line it mirrors.
- `src/evalharness/labelspaces.py`: class lists transcribed verbatim from production.
- `src/evalharness/compare.py`: paired comparison, 2x2 disagreement table, coverage refusal.
- `src/evalharness/keys.py`: HMAC item keys and the guard that refuses customer columns.
- `src/evalharness/manifest.py`: blocking versus recorded fields, and the `RECONCILED` stamp.
- `src/evalharness/paths.py`: runtime refusal of any data directory inside a git worktree.
- `src/evalharness/adapters/retention.py`: **the only file that changes when real data arrives.**
- `src/evalgen/cli.py`: **the only place the pieces are wired together**, and therefore
  the only place a mistake about how they fit can be made. Its docstring holds the
  wiring diagram and the four decisions no single module can make, starting with
  "the aggregate metrics table is scored on replicate 1, and says so".
- `src/evalgen/runner.py`: the call loop, every item and every replicate in testset
  order. It calls the model and judges nothing. Its numbered refusals are the point:
  never retry a parse failure, never drop an item, never reorder, never assume the model
  that answered is the model that was asked for.
- `src/evalgen/runtime.py`: immutable runtime identity, request dialect, endpoint safety,
  credential-variable name and reproducibility fingerprint for OpenRouter or internal GPU.
- `src/evalgen/contracts.py`: versioned application meaning: dimensions, comparison
  grain, asset/code references and the decision policy an arm claims to implement.
- `src/evalgen/artifacts.py`: private/shareable destination policy, atomic writes,
  integrity hashes and the started/result journal used for crash-safe resume.
- `src/evalgen/outcomes.py`: **the single place `parse_ok` is decided.** A second,
  slightly different copy written inline at a call site is how two arms end up with two
  denominators and a comparison that is not paired.
- `src/evalgen/flatten.py`: the grain change, **one row per PRODUCT, not per call**. A
  grain error does not raise and does not log; it produces a number, so the tests pin the
  row count before they pin any value.
- `src/evalgen/decoding.py`: the schema production *declares* is not the schema a decoder
  is asked to *enforce*. Three named deviations, each recorded with its measured before
  and after.
- `src/evalgen/prompts.py`: the retention prompt, assembled from committed sha-pinned
  assets rather than read live out of `production-reference/`, so a change to the
  reference tree is a diff a reviewer sees.
- `src/evalgen/report.py`: **a per-mechanism verdict table, not a percentage.** Section 6
  (cost, tokens, latency) sits after the aggregates on purpose.
- `src/evalgen/evidence.py`: whether the model's `keyword` spans are in the transcript, at
  two grains. A deterministic diagnostic, **not a fourth scored dimension**: two rates and
  no blended one, no verdict, one run directory at a time.
- `src/evalgen/fabrication.py`: how many reason labels a run invented, and how many of
  them the prompt's worked example handed it. Also a diagnostic, **not a scored dimension**.
- `src/evalgen/judge.py`: an independent model's opinion on scorer disagreements. Also a
  diagnostic, **not a scored dimension** -- but unlike the two above, isolation from the
  verdict path is enforced by an AST test (`tests/test_judge.py`) rather than only
  claimed in the module's own docstring. Hand-computed expectation for its
  parsing/aggregation arithmetic in `tests/fixtures/judge/HAND-COMPUTED.md`, written
  before the module, per the rule below.
- `src/evalgen/experiments.py`: the Gate 1/Gate 2 provider-qualification and
  decision-rule pipeline behind Experiments 5B and 7 (`experiments/retention-e5.plan.json`,
  `experiments/retention-e7.plan.json`).
  `decision()` is the final PASS/FAIL/INCONCLUSIVE/UNAVAILABLE call; see `EXPERIMENTS.md`
  Experiment 6 for a gap this project found and fixed in it (an UNDERPOWERED stability
  verdict silently reaching PASS) and two it recorded rather than fixed (see DEVLOG.md,
  Known Bugs).
- `src/evalgen/client.py`: the only file in `src/` that imports `openai`, so every other
  module here stays importable and testable with the SDK absent. The other side of the
  boundary `tests/test_boundary.py` guards.
- `tests/fixtures/testsets/`: the synthetic Thai packs `evalgen` runs against.
  `retention_v1.*` (20 items, 22 scored rows) is frozen and is what Experiments 1-2 used;
  `retention_v2.*` (100 items, 108 scored rows) is Experiment 3's; `retention_v3.*`
  (138 items, 150 scored rows) is the robustness pack used by Experiments 5 and 7. Those
  three form a byte-exact prefix chain, discovered by glob in `tests/test_testset_pack.py`.
  `retention_challenge_v1.*` (50 items, 64 scored rows, `RTC-*`) is **not** part of that
  chain and is deliberately not named `v4`: longer original multi-turn calls, its own
  gate in `tests/test_retention_challenge_pack.py`, plan in
  `docs/retention-challenge-v1-plan.md`. No experiment has used it yet.
- `tests/fixtures/WORKED-COMPUTATION.md`: the hand arithmetic every expectation comes from.
- `tests/production_ref.py`: test-only scaffolding to import True's real scorer.
- `tests/test_boundary.py`: turns "the scoring library makes no model calls" from prose
  into something CI can fail on, by parsing imports instead of executing them.
- `scripts/run_index.py` and `RUNS.md`: `out/` is gitignored, so every `run_id` quoted in
  `EXPERIMENTS.md` pointed into a directory a reader could not see. The script reads only
  `run.json` provenance, never `run.jsonl` where the model text lives, and writes the
  committed index. `--check` exits 1 when it is stale, so the index can be verified rather
  than trusted. Regenerate it; never hand-edit it.
- `scripts/openrouter-smoketest/`: exploratory model-calling tooling. See "Service
  layers do not apply" below for why it lives outside `src/`.
- `docs/experiment7-results.md` and `experiments/evidence/retention-e7/summary.json`:
  the current synthetic result and safe aggregate handoff. Raw/private runtime evidence
  remains ignored under `out/`.

## Conventions

- **Code style**: PEP 8. No formatter or linter is configured yet (see Open items).
- **Testing**: `pytest`. See [TESTING.md](./TESTING.md).
- **Commits**: Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- **Dependency pins are load-bearing, not cosmetic.** Under pandas 3.x the production
  scorer's `dtype == 'object'` guard is False, string normalisation never fires, and
  `call_result` accuracy collapses from 0.75 to 0.25. Never relax the pins to make an
  install succeed.
- **Never edit an expectation to make a test pass.** `tests/fixtures/retention_expected.csv`
  was hand-computed before the code existed. If a test fails, the first question is
  whether the fixture is wrong, not the code. Changing an expectation to match output
  destroys the only thing that makes the numbers credible.

## Service layers do not apply to the scoring library, and here is why

Canon mandates OpenRouter for AI routing, WorkOS for identity, and Composio for tool
execution. **None applies to `src/evalharness/`, and their absence there is not an
oversight.**

| Layer | Status in `src/evalharness/` | Reason |
|---|---|---|
| OpenRouter | not used | The scoring library makes **no model calls at all**. `src/evalgen/` uses OpenRouter as its default hosted runtime or a declared internal OpenAI-compatible endpoint. |
| WorkOS | not used | No users, no sessions, no HTTP surface. |
| Composio | not used | No external API execution. |

**`scripts/openrouter-smoketest/` is the one model-calling thing outside `src/`, and it
proves the rule rather than breaking it.** (It read "the one deliberate exception" when
the only model call in the repository was this script's; `src/evalgen/` is now the
other, and it is inside `src/` by design rather than by exception.) The script exists to
answer one question, whether an OpenRouter key works through the OpenAI SDK, asked
before any real candidate-generation work happened. It is exploratory tooling: separate
`requirements.txt`, no import from `src/`, and it produces no scored
output. Per canon, since it does make a model call, it correctly routes through
OpenRouter rather than a provider SDK directly.

If a future version runs candidate models as part of a real evaluation pipeline (not
just a pipe test), that is new work with its own review, not an extension of this
script, and OpenRouter becomes a real dependency of `src/` at that point.

**That condition has now occurred.** `src/evalgen/` is that pipeline: it runs candidate
models, and OpenRouter is a real dependency of `src/` accordingly. The paragraph above
is left standing rather than rewritten, because it is the test the change was measured
against, and it was met on its own terms:

- It is **new work with its own package**, not an extension of the smoke test. Nothing
  in `src/evalgen/` imports `scripts/openrouter-smoketest/`, and nothing imports the
  other way. `config.py` duplicates roughly forty lines of the script's key handling
  and its docstring says why: an import in either direction would end the script's
  standalone status, which is the whole reason the exception survived review.
- **The default hosted route is still OpenRouter**, not a provider SDK. A reviewed
  generic runtime manifest is the deliberate path to the company GPU; `client.py`
  remains the single SDK boundary.
- **The scoring library was not touched.** `src/evalharness/` still makes no model
  calls, and `tests/test_boundary.py` now fails CI if that changes.

What narrowed, then, is the claim: not "no model calls in `src/`" but "**no model calls
in the scoring path**". That is the line worth defending, and `tests/test_boundary.py`
is what defends it.

## Cross-Project References

> These live in canon and apply to all projects. Linked, never restated here.
> The current workspace source is `/home/tkhongsap/my-github/s42/canon`; canon is not
> vendored into this repository.

| Resource | Location | Use When |
|----------|----------|----------|
| **Project structure and repo policy** | `/home/tkhongsap/my-github/s42/canon/guides/project-structure.md` | Repo location, visibility, layout |
| **Git workflow** | `/home/tkhongsap/my-github/s42/canon/guides/git-workflow.md` | Branching, commits, PRs |
| **Production patterns** | `/home/tkhongsap/my-github/s42/canon/guides/production-patterns.md` | If this ever grows a service |
| **Release versioning** | `/home/tkhongsap/my-github/s42/canon/guides/release-versioning.md` | Tagging a version |
| **Development process** | `/home/tkhongsap/my-github/s42/canon/development/development-process.md` | Session workflow |
| **Infrastructure service layers** | `/home/tkhongsap/my-github/s42/canon/guides/infrastructure-service-layers.md` | Before adding any AI, auth or integration |

## Review Guidelines

- Automated PR review uses CodeRabbit plus Codex where enabled; humans remain merge authority.
- Treat as high-risk in this repository specifically: anything touching
  `keys.py`, `paths.py`, or `.gitignore`, because those three are what keep customer
  identifiers out of git; and any change to `metrics.py` that alters a denominator.
- Use `canon/operations/ai-pr-review-loop.md` for agent-facing policy.

## Open items

**Repository location: a deliberate, owner-approved departure from canon.**

`canon/guides/project-structure.md` states repos MUST be under the `ObjectiveFunction`
org, MUST be private, and must never sit on a personal account. This repository is
**private** (satisfied) but lives at `tkhongsap/model-eval-harness` (a departure).

**Decision, 2026-08-04, by the owner: stay on the private personal account.** The
alternative that canon prescribes, `ObjectiveFunction`, is the studio org, so moving
there would relocate True Corp work product into a studio namespace: a different
governance question rather than a fix. Moving to True's own GitHub Enterprise is
plausibly the right long-term home and remains blocked on the licence and org
questions tracked separately.

Recorded rather than left implicit so a later reader can tell this was weighed and
settled, not overlooked. Two conditions that make it defensible, and that should be
re-checked if either changes:

- The repository **must stay private**. It cites internal SharePoint site names,
  production file paths and line numbers — and, since 2026-08-14, True's internal Token
  Factory endpoint: its hostname, the RFC1918 address the probes pin
  (`token-factory-probe.ps1`, `scripts/token_factory_probe.py`) and the support contact
  named in the vendored `Token_Factory_API_Guide.md`. That is a **new category** of
  internal detail — network topology and a named individual — so it is listed here rather
  than folded into "file paths". No credential is committed: the key lives only in the
  gitignored `.env`, and `.env.example` carries an empty `TOKEN_FACTORY_API_KEY=`.
- It contains **no customer data**, and the controls in `keys.py`, `paths.py` and
  `.gitignore` exist to keep it that way. Fixture phone numbers come from the synthetic
  block `^0810000[0-9]{3}$` (`src/evalgen/testsets.py:135`), `0810000000`–`0810000999`,
  outside anything True's systems issue. **188 of the 1000 are used, 812 free** (measured
  2026-08-12): `0810000000`–`0810000099` for `retention_v1`/`v2` and the `block_*`
  fixtures, `0810000101`–`0810000138` for `retention_v3`'s phase two, and
  `0810000201`–`0810000250` for `retention_challenge_v1`. The block was widened from the
  `08100000xx` hundred on 2026-08-06 once `retention_v2` exhausted it; the wider pattern
  admits every number the narrower one did, so no committed value changed. *(Corrected
  2026-08-12 — this read "100 of the 1000 are used, all of them in the `08100000xx`
  hundred", which the widening's own first use made false. Everything remains inside the
  sanctioned block; what was wrong was the count, which is the thing that tells anyone
  whether the block is drifting.)*

Revisit if the True GitHub Enterprise org becomes available, or if anyone outside
True needs access.

**No linter, type checker or `pyproject.toml`.** Canon's Python CI template expects
ruff, mypy and a `pyproject.toml` under `backend/`. This repository has none. CI runs
the test suite only; see the comment block in `.github/workflows/ci.yml`.

## Project-Specific Notes

**Latest synthetic decision (Experiment 7, 2026-08-08): retain Gemini as the reference.**
All three arms completed 414/414 parse-valid calls. Qwen3.6 27B failed stability; Qwen3.6
35B-A3B failed quality and stability. The independent judge returned 360 advisory
opinions and flagged 38 possible ground-truth errors for human review. See
`docs/experiment7-results.md`. This does not change `RECONCILED: NO`.

**No number this harness produces is a migration verdict yet.** Every report prints
`RECONCILED: NO` until a run has been checked against the app's own live Gemini
fact-check report, which needs real data this repository does not have. The stamp
exists so the harness cannot launder its own provenance under schedule pressure. Do
not remove it to make a report look finished.

**The workbook adapter is a known unknown.** `load_workbook()` raises
`NotImplementedError` on purpose: production reconstructs column names from a two-row
header block nobody here has seen, and a loader written against a guess would appear
to work while silently mis-mapping columns. Fix it when the header rows arrive, not before.
