## Outcome

<!-- What decision or capability changes? Lead with the observable result. -->

## Scope and source of truth

- Authoritative source or contract:
- In scope:
- Explicitly out of scope:
- Application / dataset / prompt / decision policy affected:

## Evaluation integrity

- [ ] I did not edit a hand-computed expectation merely to make a test pass.
- [ ] I identified every denominator or grain change (`product row`, `call`, or
      `call cluster`) and added an independently derived expectation.
- [ ] Paired coverage and runtime/application identities remain comparable.
- [ ] The LLM judge remains advisory and isolated from model scores and decisions.
- [ ] `RECONCILED` remains `NO`, or this PR includes approved production reconciliation
      evidence and explains who accepted it.

## Privacy and runtime safety

- Data classification: <!-- synthetic / internal / customer -->
- Runtime/network behavior: <!-- offline / OpenRouter / internal GPU -->
- [ ] No credential, raw completion, customer transcript, direct identifier, cited
      private judge span, or unrestricted `out/` artifact is committed.
- [ ] Any committed experiment evidence is aggregate, synthetic/shareable, and passes
      the repository's shareability checks.
- [ ] Changes to `keys.py`, `paths.py`, `.gitignore`, runtime manifests, destinations,
      or HMAC handling are called out for focused review.

## Verification performed

```text
PYTHONPATH=src python -m pytest tests/ -q -rs
PYTHONPATH=src TRUE_SOURCE_ROOT=<path> python -m pytest tests/ -q -rs
PYTHONPATH=src python scripts/evalgen.py experiment-check --plan <plan>
git diff --check
```

- Standalone result and skip reasons:
- Production differential result or reason unavailable:
- Offline plan/artifact validation:
- Additional focused checks:

## Handoff

- Files teammates should read first:
- Exact rerun command:
- Known limitation or blocker:
- Next owner and action:

## Canon exceptions

<!-- Link the relevant canon path. Record any deliberate exception and owner decision;
do not silently copy or weaken canon. Workspace source:
/home/tkhongsap/my-github/s42/canon -->
