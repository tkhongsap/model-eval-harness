# Team runbook: reproducible model evaluation on OpenRouter or internal GPU

This is the operational path for moving an evaluation arm from a hosted gateway to a
company GPU without changing what the scorer means. The serving runtime is replaceable;
the testset, prompt, decoding contract, item order, replicate count, scorer, and decision
policy remain reviewable inputs.

The current application contract is retention. The runtime contract is deliberately
provider-neutral: OpenRouter and an OpenAI-compatible endpoint (for example vLLM, TGI,
SGLang, or another compatible gateway) use the same generation and scoring pipeline.
This repository does not install or operate the GPU server itself.

Runtime identity and application identity are separate contracts. A runtime says where
and how a model was served; an application contract says which dimensions, decision
units, normalized record grain, prompt, schema, testset, adapter, and default quality
policy give the evaluation its meaning. The retention preset is versioned and
fingerprinted. Retention is still the only fully wired application today; the contract
prevents a future application from being implemented as an undocumented collection of
retention-specific exceptions.

> **Current status — setup READY; migration INCONCLUSIVE.** The team can install,
> preflight, and execute this pipeline. A decision still requires a real company-GPU
> arm, approved real data, and reconciliation against the application's live Gemini
> fact-check report. Until then, `RECONCILED: NO` is a real gate, not report decoration.

The adopted defaults are: one independent paired unit per call; exact-set reason and
product correctness with per-label diagnostics; conservative `decision_grade_v2`;
an advisory-only independent judge; private raw artifacts outside Git; and a
non-secret, fingerprinted runtime contract that can describe OpenRouter or a company
GPU endpoint. Historical behavior remains available only through explicit legacy
paths.

## 1. Non-negotiable boundaries

- `src/evalharness/` scores and has no model client or network dependency.
- `src/evalgen/` calls the selected runtime and records what happened.
- Never put customer data, transcripts, raw model output, API keys, or private judge
  records in Git. `data/`, `out/`, and `build/` are ignored as a fallback, not approved
  storage locations for customer data inside the worktree.
- Set `EVAL_HARNESS_DATA_DIR` to an existing directory outside every Git worktree.
  `internal` and `customer` runs are refused if their destination is elsewhere.
- A runtime manifest contains only non-secret configuration. `api_key_env` is the name
  of an environment variable; its value is resolved only when a real client is built.
- Keep incumbent and candidate inputs paired. Do not drop parse failures, orphan rows,
  or failed calls to make coverage look better.
- The independent LLM judge is advisory evidence about disputed labels. It is never a
  fourth scored dimension and never changes PASS/FAIL by itself.

For a future application, add a new adapter, prompt/schema/testset references, explicit
scored dimensions and decision units, hand-computed expectations, and an immutable
`ApplicationSpec`. Do not fork the runtime client or reuse retention labels under a new
name. The same runtime manifest should be able to execute any fully wired application.

## 2. Create separate environments

Use Python 3.12. Keeping the environments separate preserves the network-free scoring
boundary and avoids widening the resolver that holds True production's load-bearing
pandas/numpy/openpyxl pins.

```bash
# Scoring, production differential, reports, and the complete test suite.
python3.12 -m venv .venv-score
.venv-score/bin/python -m pip install -r requirements.txt

# Generation only: this is the environment allowed to reach a model endpoint.
python3.12 -m venv .venv-gen
.venv-gen/bin/python -m pip install -r src/evalgen/requirements.txt
```

On Windows, replace `.venv-*/bin/python` with `.venv-*\\Scripts\\python.exe`.
Do not relax the root production pins to make installation succeed.

Run the offline checks before configuring a credential:

```bash
PYTHONPATH=src .venv-score/bin/python -m pytest \
  tests/test_boundary.py tests/test_requirements.py -q
PYTHONPATH=src .venv-score/bin/python -m pytest \
  tests/test_runtime.py tests/test_artifacts.py tests/test_runner.py tests/test_paths.py -q
```

In PowerShell, set `$env:PYTHONPATH = 'src'` once before these commands. The explicit
path is necessary while the repository has no `pyproject.toml` or editable install.

## 3. Prepare private storage

The directory must already exist and resolve outside the repository (and outside any
other Git worktree).

```bash
export EVAL_HARNESS_DATA_DIR=/srv/model-eval-harness-data
mkdir -p "$EVAL_HARNESS_DATA_DIR/runs" "$EVAL_HARNESS_DATA_DIR/judge"

# Required only by operations that emit stable customer-item keys.
export EVAL_HARNESS_KEY_HMAC='<obtain from the True team secret store>'
```

On PowerShell:

```powershell
$env:EVAL_HARNESS_DATA_DIR = 'D:\model-eval-harness-data'
New-Item -ItemType Directory -Force "$env:EVAL_HARNESS_DATA_DIR\runs" | Out-Null
```

Credentials belong in the shell or the team's secret manager, never in a manifest,
command transcript, committed `.env`, or report.

## 4. Describe the runtime

Copy [the local-GPU example](../configs/runtime.local-gpu.example.json) to a controlled
configuration location and replace every placeholder before a decision-grade run.

```bash
cp configs/runtime.local-gpu.example.json \
  "$EVAL_HARNESS_DATA_DIR/runtime.true-gpu-staging.json"
```

Record immutable deployment facts wherever they are available:

- `model_revision`: weights revision or content digest;
- `image_digest`: immutable serving-container digest, not a mutable tag;
- `server_version`: vLLM/TGI/SGLang/gateway version;
- `gpu_type`: hardware class;
- any team-owned deployment or quantization identifier needed to reconstruct the arm.

The canonical non-secret manifest is SHA-256 fingerprinted and copied into run
provenance. Placeholder values are valid strings and therefore also affect the
fingerprint: replace them; do not treat the example itself as production identity.

Before qualification, the serving team should confirm the endpoint contract rather
than infer it from the words “OpenAI compatible”:

- `/v1/chat/completions` accepts the selected model ID and the standard fields emitted
  by the reviewed dry-run;
- the server enforces or explicitly rejects the requested structured-response schema;
- model identity and token usage are returned when supported, and any missing identity
  evidence is treated as a recorded limitation;
- authentication, TLS/network policy, request-size limits, timeouts, and concurrency
  limits are owned and documented by the serving team;
- weights, quantization, tokenizer/chat template, server build, and GPU topology are
  immutable or versioned for the duration of an arm.

“Compatible” is established by the qualification evidence, not by the server product
name. If the server cannot honor a reviewed decoding field or structured schema, record
that as an incompatibility or preregister a new workload; do not silently translate the
request for one arm.

### Internal OpenAI-compatible GPU

The manifest is the preferred team hand-off because it is reviewable:

```bash
export EVALGEN_GPU_API_KEY='<runtime credential or server-required placeholder>'

.venv-gen/bin/python scripts/evalgen.py baseline \
  --arm candidate-gpu \
  --model '<model id exposed by the server>' \
  --runtime-manifest "$EVAL_HARNESS_DATA_DIR/runtime.true-gpu-staging.json" \
  --data-classification internal \
  --out "$EVAL_HARNESS_DATA_DIR/runs"
```

The equivalent flag-based form is useful for one-off staging checks:

```bash
.venv-gen/bin/python scripts/evalgen.py baseline \
  --arm candidate-gpu \
  --model '<served model id>' \
  --runtime-backend openai-compatible \
  --runtime-id true-gpu-staging-v1 \
  --base-url 'https://gpu-gateway.example.internal/v1' \
  --api-key-env EVALGEN_GPU_API_KEY \
  --runtime-metadata 'model_revision=<immutable revision>' \
  --runtime-metadata 'image_digest=sha256:<immutable digest>' \
  --runtime-metadata 'server_version=<version>' \
  --runtime-metadata 'gpu_type=<hardware class>' \
  --data-classification internal \
  --out "$EVAL_HARNESS_DATA_DIR/runs"
```

Plain HTTP is accepted automatically only on loopback. For an isolated, explicitly
trusted private network, `--allow-insecure-http` is a conscious opt-in; prefer TLS.
OpenRouter-only provider routing and normalized reasoning options are refused by a
generic runtime instead of being silently ignored.

### OpenRouter

The historical route remains supported. Pin a provider when the experiment requires a
single backend, because a model ID alone does not identify the serving build.

```bash
export OPENROUTER_API_KEY='<secret>'

.venv-gen/bin/python scripts/evalgen.py baseline \
  --arm incumbent \
  --model '<OpenRouter model id>' \
  --runtime-backend openrouter \
  --provider '<measured provider name>' \
  --data-classification internal \
  --out "$EVAL_HARNESS_DATA_DIR/runs"
```

## 5. Preflight without a model call

Validate the committed pack and prompt first:

```bash
.venv-gen/bin/python scripts/evalgen.py check
```

Then render the exact request bodies through the same request builder the real client
uses. `--dry-run` reads no key and makes zero API calls:

```bash
.venv-gen/bin/python scripts/evalgen.py baseline \
  --arm candidate-gpu-smoke \
  --model '<served model id>' \
  --runtime-manifest "$EVAL_HARNESS_DATA_DIR/runtime.true-gpu-staging.json" \
  --data-classification synthetic \
  --out "$EVAL_HARNESS_DATA_DIR/runs" \
  --dry-run
```

Review the emitted prompt, request JSONL, runtime fingerprint, model ID, decoding
parameters, item count, and estimated workload. A synthetic dry-run proves request
construction and local wiring; it does not prove endpoint compatibility or quality.

## 6. Execute, checkpoint, and resume

For a real run, point `--testset` and `--gt` at approved inputs under
`EVAL_HARNESS_DATA_DIR`, declare the true classification, and keep the output there.
Before the first call the runner snapshots the effective testset, ground truth, prompt,
and decoding schema into the run directory. For each item/replicate cell it fsyncs a
`started` event before dispatch and a `result` event after the call returns.

A completed run contains, at minimum:

- immutable input snapshots and their hashes;
- `run.journal.jsonl`, the crash-resume evidence for each completed cell;
- `run.jsonl`, the canonical complete result matrix;
- `run.json`, provenance, outcome counts, runtime/dependency identity, and artifact
  hashes;
- run state that reaches `COMPLETE` only after integrity checks pass.

The completed directory is the portable evidence bundle. Its authoritative testset,
ground-truth, schema, and prompt references are run-relative (`inputs/...`), and the
loader resolves them against the bundle root. Original source locations are provenance,
not dependencies. Copy or move the entire directory unchanged; moving only `run.json`
or `run.jsonl` discards evidence and is refused. After transfer, load or compare the run
once at its new location to recheck every recorded hash. Historical pre-bundle runs may
still contain absolute paths; they are readable for compatibility but are not made
portable retroactively.

If execution stops, rerun the same command with the same contract and add the exact
incomplete run directory:

```bash
.venv-gen/bin/python scripts/evalgen.py baseline \
  --arm candidate-gpu \
  --model '<served model id>' \
  --runtime-manifest "$EVAL_HARNESS_DATA_DIR/runtime.true-gpu-staging.json" \
  --testset "$EVAL_HARNESS_DATA_DIR/inputs/testset.jsonl" \
  --gt "$EVAL_HARNESS_DATA_DIR/inputs/ground-truth.csv" \
  --data-classification customer \
  --out "$EVAL_HARNESS_DATA_DIR/runs" \
  --resume-run "$EVAL_HARNESS_DATA_DIR/runs/<incomplete-run-directory>"
```

Resume accepts only the original contract and skips only cells with valid durable
results. A durable `started` event without a durable result is unresolved: the server
may have completed the call, so replaying it would silently select a second draw. A
torn final JSON record is detected; earlier complete events remain readable, but
automatic resume refuses rather than infer what the partial append contained. Preserve
that directory for review and start a new run.
Do not edit a journal, snapshot, result log, or `run.json` to repair a run. Start a new
run when a model, runtime, prompt, input, decoding parameter, or execution setting
changes.

## 7. Compare the paired arms

Only compare complete runs that pass artifact integrity, exact item identity/coverage,
scoring-code, testset, ground-truth, prompt, and generation-contract checks.

The two arms share a content-addressed evaluation workload: application contract,
testset, ground truth, prompt, response schema, replicate design, optional locked
experiment plan, and the outcome-classification identity that interprets responses.
Model, provider, runtime fingerprint, decoding controls, concurrency, timeout, retry
policy, and other serving/execution facts are recorded separately per arm. Intended arm
differences remain visible in provenance and the report; they are not rewritten to make
two systems look identical. Comparability means both systems answered the same
evaluation question, not that they ran on the same stack. A locked experiment may still
require selected per-arm settings to match; that requirement belongs in the reviewed
plan rather than being hidden inside the common workload hash.

```bash
.venv-score/bin/python scripts/evalgen.py compare \
  --incumbent "$EVAL_HARNESS_DATA_DIR/runs/<incumbent-run>" \
  --candidate "$EVAL_HARNESS_DATA_DIR/runs/<candidate-run>" \
  --report "$EVAL_HARNESS_DATA_DIR/runs/<comparison>/report.txt"
```

Keep the report private until its shareability has been reviewed. The aggregate table
is scored on replicate 1; stability and operational accounting use all replicates. The
three quality dimensions retain their own denominators.

`compare` assembles paired evidence and mechanism results; it does not by itself create
an enterprise migration decision. A decision-grade GPU migration needs a new locked,
reviewed plan whose run assignments and operational load evidence are passed to
`experiment-report`. Do not retrofit the historical retention Experiment 5 plan around
a new runtime after seeing its results.

## 8. Decision policy

Decision-grade inference is clustered once per call. The normalized scorer can still
hold several product rows for one transcript, and regression review keeps the
actionable row detail, but those correlated rows do not become several independent
customers in the paired exact test. An arm is correct for the call cluster only when it
is correct on every scored unit in that call.

For reasons and products, exact set correctness is the primary paired outcome: missing
one expected label or adding one unsupported label makes that unit wrong. Per-label
TP/FP/FN/TN, recall, and F1 remain important diagnostics for understanding the error,
but they are not promoted into extra independent decision units. Orphan claims, missing
outputs, invalid parses, duplicate keys, and mismatched identity coverage are retained
or refused explicitly rather than disappearing from a denominator.

New decision-grade reports use `decision_grade_v2` by default. It fails closed:

- exactly one paired verdict is required for every expected quality dimension plus
  stability;
- invalid runtime, identity, provenance, or paired-evidence structure is
  `INCONCLUSIVE`, not a model-quality failure and never PASS;
- reliability below the gate fails the candidate (and makes an unreliable incumbent
  inconclusive evidence);
- `BEHIND` fails; `UNDERPOWERED` is inconclusive;
- an `INDISTINGUISHABLE` result with a negative observed net cannot PASS beyond the
  preregistered per-dimension allowance, which defaults to zero.

This observed-loss guard is conservative; it is not a confidence interval or a claim
of statistical non-inferiority.

`legacy_v1` exists only to reproduce historical Experiment 5 reports. A caller must
request it explicitly in the decision API. Do not use it for a new migration decision:
it preserves historical semantics and does not have the v2 completeness and net-loss
protections.

## 9. Independent judge: private evidence and shareable export

Run the judge only after the primary comparison is valid, with a model that is not
either evaluated arm. First count the disagreement units without a key or call:

```bash
.venv-gen/bin/python scripts/evalgen.py judge \
  --incumbent "$EVAL_HARNESS_DATA_DIR/runs/<incumbent-run>" \
  --candidate "$EVAL_HARNESS_DATA_DIR/runs/<candidate-run>" \
  --model '<independent judge model>' \
  --provider '<pinned provider when using OpenRouter>' \
  --dry-run
```

The full private judge bundle can contain transcript-derived cited spans, rationales,
exact requests, and raw model responses. Store it only below
`EVAL_HARNESS_DATA_DIR/judge`. The shareable judge view intentionally removes cited
spans, rationales, requests, and raw response text; it retains stable judgment-unit IDs,
hashes, execution/identity status, aggregate counts, and source provenance. Run the
recursive shareability guard before moving any aggregate artifact out of private
storage.

For the real advisory run, request both surfaces explicitly. This example uses a
provider-pinned OpenRouter judge independently of the internal-GPU candidate:

```bash
.venv-gen/bin/python scripts/evalgen.py judge \
  --incumbent "$EVAL_HARNESS_DATA_DIR/runs/<incumbent-run>" \
  --candidate "$EVAL_HARNESS_DATA_DIR/runs/<candidate-run>" \
  --model '<independent OpenRouter judge model>' \
  --provider '<measured provider name>' \
  --runtime-backend openrouter \
  --data-classification customer \
  --private-out "$EVAL_HARNESS_DATA_DIR/judge/<judge-run>.private.json" \
  --shareable-out "$EVAL_HARNESS_DATA_DIR/judge/<judge-run>.shareable.json"
```

`--private-out` is the restricted review record and raw journal. `--shareable-out` is
the sanitized export checked recursively before it is written. Keep even the sanitized
file in private storage until a human has reviewed its classification and source
provenance; “shareable” means its schema excludes the known private fields, not that an
operator has approved a destination or audience.

A judge result can flag a ground-truth issue for human review. It cannot resolve the
issue automatically, alter the three scored dimensions, or turn an inconclusive/failed
comparison into PASS.

## 10. Staged migration checklist

The repository is currently ready for steps 1–3. Steps 4–9 require the company GPU,
approved real data, and application-owner reconciliation; until those gates finish, the
only defensible migration status is `INCONCLUSIVE`.

1. **Offline confidence** — install both environments; pass boundary, pin, runtime,
   artifact, runner, and scorer tests.
2. **Runtime identity** — review the runtime manifest; replace every placeholder; record
   immutable weights, container, server, and GPU facts; store secrets separately.
3. **Synthetic preflight** — run `check` and a baseline `--dry-run`; review exact request
   bodies and prompt; make no network call.
4. **Endpoint qualification** — use a small synthetic smoke slice; confirm requested and
   observed model/runtime identity, schema support, token accounting, and parse behavior.
5. **Frozen paired evaluation** — freeze the common workload plus each arm's declared
   decoding and execution settings, decision policy, and allowed net-loss margins before
   the full arms run.
6. **Full generation** — run incumbent and candidate into private external storage;
   resume only from validated journals; never edit paid evidence.
7. **Comparison and decision** — verify complete artifact matrices and exact coverage,
   produce the paired comparison, then apply `decision_grade_v2` only through the locked
   decision plan and its complete operational evidence.
8. **Advisory review** — optionally run an independent judge on disagreement units and
   send all ground-truth-error flags to a human reviewer.
9. **Production reconciliation** — reconcile against the live application fact-check
   report. Until this is complete, retain `RECONCILED: NO` and make no migration claim.
10. **Promotion record** — preserve the approved runtime fingerprint, model revision,
    decision-policy hash, report hashes, human approvals, rollback owner, and monitoring
    plan. Any material runtime or weights change starts a new evaluation.

## 11. Team hand-off record

For every run shared with the team, include:

- Git commit and dirty-worktree status;
- run ID, arm/role, application, and data classification;
- application-contract manifest/fingerprint, including dimension decision units and
  normalized record grain;
- model ID, runtime ID/fingerprint, requested and observed serving identity;
- immutable runtime metadata and dependency/Python provenance;
- testset, ground-truth, prompt, decoding, generation, scorer, and decision-policy
  hashes;
- item/replicate matrix, parse/transport counts, and whether the run was resumed;
- confirmation that the whole portable bundle was retained, its relative input
  references resolve, and its journal has no unresolved call or torn record;
- private artifact location and separately reviewed shareable artifacts;
- decision policy, explicit net-loss allowances, reconciliation status, and human owner.

That record makes a future GPU rerun comparable without making this repository specific
to one vendor, server implementation, model family, or hardware generation.
