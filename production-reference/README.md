# Source Code Review — Extracted Repositories

Extracted from `Source Code Review.zip` on 2026-08-02. This folder contains **four production Python codebases** (~55k lines) that power True's internal Gemini-based AI applications. They are the subject of the [local LLM platform migration](../README.md).

## What this is (one paragraph)

These are **batch AI pipelines** that run on Google Cloud (Cloud Run Jobs + Cloud Scheduler). They pull files from **SharePoint** or **S3**, stage them in **GCS**, send them to **Google Gemini via the Batch API**, parse structured JSON responses, and write **Excel reports** back to SharePoint. There is **no separate speech-to-text step** — audio is sent directly to Gemini as `audio/wav`. The migration project is about replacing Gemini with on-prem open models while keeping PDPA/data-residency compliance.

## The four repos

| Folder | What it does | Apps inside | ~LOC | Tests |
|--------|-------------|-------------|------|-------|
| [`sentiment-voice-analysis-develop/`](sentiment-voice-analysis-develop/) | Modular task framework — the most mature codebase | Telesale QA, Sentiment QA, OCR tax invoice, tax reconcile | 33k | 93 files |
| [`rtr-fraud-validation-main/`](rtr-fraud-validation-main/) | Image fraud detection for retailer shop registrations | RTR fraud + Pulse | 7k | 21 files |
| [`sentiment-batch-retention-main/`](sentiment-batch-retention-main/) | Thai call-churn analysis for retention campaigns | Sentiment retention | 10k | 0 |
| [`sentiment-batch-mnp-develop/`](sentiment-batch-mnp-develop/) | Same pattern as retention, for MNP (number porting) | Sentiment MNP | 5.5k | 0 |

## Common architecture pattern

Every repo follows the same high-level flow:

```
SharePoint / S3  →  GCS staging  →  build JSONL batch payload
    →  submit Gemini Batch job  →  poll until done
    →  retrieve results from GCS  →  parse JSON  →  Excel report → SharePoint
```

Scheduled as **overnight batch jobs** (not real-time). Cloud Scheduler triggers Cloud Run Jobs on a cron.

## Where to start reading

### If you want the big picture first
1. Read each repo's own `README.md` (linked above).
2. Read the analysis doc: [`../2026-07-24-source-code-review.md`](../2026-07-24-source-code-review.md) — key findings on batch API dependency, no ASR, eval harness, residency.

### If you want to trace one pipeline end-to-end

**Voice / sentiment (best-engineered repo):**
- Entry: `sentiment-voice-analysis-develop/main.py`
- Engine: `src/core/engine.py` → `TaskRegistry` → individual tasks
- Gemini batch: `src/modules/gemini_batch.py`
- Example task chain: `tasks/sentiment_telesale/`
- Config: `config/sentiment_telesale/telesale_pipeline_tasks.yml`

**Fraud detection (cleanest layering):**
- Entry: `rtr-fraud-validation-main/app/main.py`
- Pipeline: `app/pipeline/fraud_pipeline.py` (ingest → process → report → publish → notify)
- Gemini calls: `app/services/gemini_service.py`
- Eval harness: `app/modules/fact_checker.py` (accuracy, precision, recall, F1)
- Config: `config/model_setting/rtr.yml`

**Retention / MNP (simpler, monolithic):**
- Entry: `sentiment-batch-retention-main/src/main.py` or `sentiment-batch-mnp-develop/src/main.py`
- Single large `main.py` with inline batch logic
- Config: `config/` (prompts + model settings)

## Key technical facts (from code review)

| Finding | Implication for migration |
|---------|--------------------------|
| **No ASR anywhere** — audio goes straight to Gemini | Apps 1–4 need a new two-stage pipeline: ASR → text analysis |
| Uses **Gemini Batch API** (submit job, poll, collect) | vLLM has no equivalent — must build job queue + state tracking |
| **SharePoint is source of truth**, GCS is staging | Microsoft dependency remains regardless of model choice |
| Model calls isolated behind service classes + YAML config | Repointing the model is a small change; orchestration is the hard part |
| **31 Cloud Run / Scheduler / Workflow references** | Migrating models ≠ migrating compute — that's a separate project |
| Eval harness exists (`fact_checker.py`) | Gate 0 quality comparison can reuse existing ground truth + metrics |
| `location: global` in at least one config | Current setup has no residency pinning — strengthens PDPA argument |

## Technology stack (shared across repos)

- **Language:** Python 3.11+
- **AI:** Google Gemini (2.5-flash, 1.5-flash, 2.0-flash) via `google-genai` SDK
- **Cloud:** GCP — Cloud Run Jobs, Cloud Scheduler, GCS, Secret Manager, Vertex AI Batch
- **Microsoft:** SharePoint (MSAL auth), Microsoft Graph API (email)
- **AWS:** S3 (fraud repo only — source images)
- **Output:** Structured JSON → Excel reports
- **IaC:** Terraform (voice repo), Cloud Build YAML (all repos)
- **Package mgmt:** `uv` (voice, fraud) or `pip` + `requirements.txt` (retention, mnp)

## Suggested learning path

1. **30 min — understand the domain:** Read `sentiment-voice-analysis-develop/README.md` § Overview and § Projects table.
2. **30 min — trace one call:** Follow `main.py` → `CoreEngine` → a telesale task → `GeminiBatchModule`.
3. **20 min — see the fraud variant:** Read `rtr-fraud-validation-main/README.md` § Project Structure, then `app/pipeline/fraud_pipeline.py`.
4. **20 min — compare the simple repos:** Skim `sentiment-batch-retention-main/src/main.py` — same logic, no framework.
5. **30 min — read the migration analysis:** [`../2026-07-24-source-code-review.md`](../2026-07-24-source-code-review.md) for what changes and what doesn't.

## Related docs in parent folder

| File | What it covers |
|------|---------------|
| [`../README.md`](../README.md) | Project overview and status |
| [`../2026-07-24-source-code-review.md`](../2026-07-24-source-code-review.md) | Detailed code review findings |
| [`../2026-07-24-architecture-v2.md`](../2026-07-24-architecture-v2.md) | Target architecture (capability routing) |
| [`../2026-07-20-planning-frame.md`](../2026-07-20-planning-frame.md) | Migration scope and quality gates |
