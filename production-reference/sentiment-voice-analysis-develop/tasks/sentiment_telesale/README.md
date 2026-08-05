# Sentiment Telesale — Task Documentation

AI-powered telesale call quality analysis pipeline built on the shared framework in `src/`. Processes voice recordings from SharePoint through Google Gemini Batch API and exports structured QA reports.

Supporting skills:
| Skill Code | Skill Name |
| -- | ------------------- |
| 1  | 01_True_CVG_DIGITAL |
| 2  | 02_True_CVG_INBOUND |
| 3  | 03_True_CVG_Post |
| 4  | 04_True_Other |
| 6  | 06_True_ExtraSim |
| 7  | 07_True_FamilySim |
| 8  | 08_True_P2P_M1 |
| 9  | 09_True_P2P_M2 |
| 10 | 10_True_UTOL |
| 11 | 11_True_UTVS |
| 12 | 12_True_Citrine |
| 13 | 13_True_Promo_End |
| 14 | 14_Proactive_Retentio |
| 15 | 15_Sale and Service |
| 16 | Family Sim |
| 17 | P2P Outbound |
| 18 | Postpaid Upsell |
| 19 | Prepaid Migrant |
| 20 | Upsell New Sim |

---

## Pipeline Variants

Three pipeline configs are available in `config/sentiment_telesale/`:

| Config File                           | Purpose                                                      |
| ------------------------------------- | ------------------------------------------------------------ |
| `telesale_pipeline_tasks.yml`         | **Main pipeline** — daily production run |
| `telesale_pipeline_evaluate.yml`      | **Evaluation pipeline** — includes evaluation report |
| `telesale_pipeline_fact_check.yml`    | **Fact-check pipeline** — standalone validation against ground truth |

Run a pipeline:

```bash
uv run python main.py --config_path config/sentiment_telesale/telesale_pipeline_tasks.yml
uv run python main.py --config_path config/sentiment_telesale/telesale_pipeline_fact_check.yml
```

---

## Task Reference

| # | Registry Name | File | Pipeline | Purpose |
|---|---|---|---|---|
| 1 | `TelesaleUploadVoiceTask` | `upload_voice_task.py` | Main | Download voice files from SharePoint → upload to GCS |
| 2 | `TelesalePrepPayloadTask` | `prep_payload_task.py` | Main | Build JSONL batch payloads for Gemini API |
| 3 | `TelesaleExecuteBatchJobTask` | `execute_batch_job_task.py` | Main | Submit batch job to Vertex AI |
| 4 | `TelesaleGetBatchResultTask` | `get_batch_result_task.py` | Main | Poll & parse predictions from completed batch |
| 5 | `TelesalePrepResultTask` | `prep_result_task.py` | Main | Score predictions using `telesale_scoring.yml` |
| 6 | `TelesaleExportOutputResultTask` | `export_output_result_task.py` | Main | Export results to SharePoint, archive GCS files |
| 7 | `TelesaleEvaluationOutputTask` | `evaluation_output_task.py` | Evaluation | Generate evaluation Excel report |
| 8 | `TelesaleFactCheckTask` | `fact_check_task.py` | Fact-check | Validate model predictions against ground truth |

---

## Task Details

### 1. `TelesaleUploadVoiceTask`

Downloads voice files from SharePoint (Verint site) and uploads them to GCS for batch processing.

**Key config params:**
- `upload_conditions` — list of `"skill/_CALLTYPE"` filters (e.g., `["13/_OUT"]` = skill 13 outbound)
- `lookback_days` — how many past days to check for unprocessed files
- `max_concurrent_uploads` — async concurrency limit

**Inputs:**
- SharePoint Verint: `${TELESALE_VERINT_ROOT}/${TELESALE_VERINT_INPUT}/%{DATA_DATE_YYYYMM}/%{DATA_DATE_YYYYMMDD}/`
- Agent master file: `${TELESALE_MASTER_PATH}/%{DATA_DATE_YYYYMM}/agentlist_YYYYMMDD.xlsx`

**Outputs:**
- GCS: `sentiment_telesale/input/%{DATA_DATE_YYYYMM}/%{DATA_DATE_YYYYMMDD}/*.wav`
- Control log (SharePoint): `${TELESALE_CONTROL_FILE_PATH}/control_file.xlsx` — tracks processed dates

---

### 2. `TelesalePrepPayloadTask`

Builds JSONL payloads by combining voice file GCS URIs with agent-specific system prompts.

**Prompt mapping flow (5 steps):**
1. Load base system prompt (`system_prompt.txt`) with `${KNOWLEDGE_BASE}` and `${CAMPAIGN_CHECKLIST}` placeholders, load common prompt (txt) and check list prompt (Excel) from SharePoint
2. Parse check list Excel (sheets: `Categories`, `Subcategories`, `Tags`) — validated with Pandera schemas (`CheckListCategorySchema`, `CheckListSchemaSubcategoriesSchema`, `CheckListTagsSchema`). Build hierarchical checklist text per `commission_skill_code` and substitute into the base prompt
3. Extract `agent_id` and `record_date` from filenames
4. Load agent master Excel from SharePoint — validated with `AgentMasterSchema`, maps `agent_id` → `commission_skill_code`
5. Merge file metadata with agent mapping and attach prompt templates. Lookup campaign-specific validation model via `ValidationMapping` (`output_validation/validate_mapping.py`) and rebuild it dynamically using active tag codes from the check list Excel

**Inputs:**
- GCS voice files: `sentiment_telesale/input/%{DATA_DATE_YYYYMM}/%{DATA_DATE_YYYYMMDD}/`
- `config/sentiment_telesale/system_prompt/system_prompt.txt`
- Common prompt file (SharePoint): `${TELESALE_USER_PROMPT_PATH}/0_common_prompt.txt`
- Check list prompt file (SharePoint): `${TELESALE_USER_PROMPT_PATH}/check_list_prompt.xlsx`

**Outputs:**
- GCS staged voices: `sentiment_telesale/processing/voice/%{DATA_DATE_YYYYMM}/%{DATA_DATE_YYYYMMDD}/` (files copied here during payload build)
- GCS JSONL: `sentiment_telesale/processing/batch/%{DATA_DATE_YYYYMM}/%{DATA_DATE_YYYYMMDD}/%{DATA_DATE_YYYYMMDDHHMMSS}/payloads.jsonl`

Each payload line:
```json
{
  "request": {
    "contents": [{"role": "user", "parts": [
      {"text": "<system_prompt_with_substitutions>"},
      {"fileData": {"fileUri": "gs://bucket/path.wav", "mimeType": "audio/wav"}}
    ]}],
    "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"}
  }
}
```

---

### 3. `TelesaleExecuteBatchJobTask`

Submits the JSONL payload to Vertex AI Gemini Batch API.

**Key config params:**
- `model_name` — Gemini model ID (e.g., `gemini-2.5-flash`)
- `gcp_project_id`, `vertex_ai_location`

**Outputs:**
- Vertex AI batch job created (polled for initial status after 5s)
- Batch processing log CSV (SharePoint): `${TELESALE_BATCH_PROCESSING_LOG_PATH}/batch_processing_log.csv`
  - Columns: `data_date`, `batch_job_id`, `batch_job_display_name`, `model_name`, `status`, `error_message`, `created_dt`, `updated_dt`

---

### 4. `TelesaleGetBatchResultTask`

Polls the Vertex AI batch job and parses completed predictions into structured payloads.

**Inputs:**
- GCS output: `sentiment_telesale/output/%{DATA_DATE_YYYYMM}/%{DATA_DATE_YYYYMMDD}/**/predictions.jsonl`

**Output payload per file:**
```python
{
  "file_metadata": {
    "file_uri": "gs://...", "file_name": "...", "call_id": "...",
    "phone_number": "...", "agent_id": "...", "record_date": "YYYYMMDD", ...
  },
  "prediction": {
    "raw_prediction": { ...campaign-specific validation fields... },
    "status": "SUCCESS|FAILED",
    "model_version": "gemini-2.5-flash",
    "token_input": {...}, "token_output": {...}
  },
  "load_dt": "YYYY-MM-DD HH:MM:SS"
}
```

---

### 5. `TelesalePrepResultTask`

Applies multi-level scoring to each prediction using `config/sentiment_telesale/telesale_scoring.yml`.

**Scoring algorithm:**
- Recursive traversal of nested YAML criteria
- Each leaf field carries a penalty (negative int) applied when the prediction value is `False` or `None`
- `max_score` — total possible points
- `max_score_not_none` — possible points excluding criteria with `None` responses
- Final score is always ≥ 0

**Scoring YAML structure:**
```yaml
category_name:
  max: 10
  subcategory_name:
    max: 5
    criterion_field: -2   # deducted if prediction[criterion_field] is False
```

**Output:** Adds `scoring_result` to each prediction:
```python
{
  "scoring_detail": {...},
  "scoring_status": "SUCCESS|FAILED|SKIPPED",
  "total_score": 8,
  "max_score": 10,
  "max_score_not_none": 10
}
```

---

### 6. `TelesaleExportOutputResultTask`

Exports final results to SharePoint and archives processed files in GCS.

**Outputs:**
- Prediction result (SharePoint): `${TELESALE_VERINT_OUTPUT}/%{DATA_DATE_YYYYMM}/telesale_prediction_result_%{DATA_DATE_YYYYMMDD}.txt` (tab-delimited)
- Raw prediction JSON (SharePoint, monitoring): `${TELESALE_RAW_PREDICTION_PATH}/%{DATA_DATE_YYYYMM}/<filename>.json` — exported when `IS_MONITORING_ENABLED=true`
- Archived voice (GCS): `sentiment_telesale/archive/voice/%{DATA_DATE_YYYYMM}/%{DATA_DATE_YYYYMMDD}/`
- Archived batch (GCS): `sentiment_telesale/archive/batch/`
- Transaction log (SharePoint): `${TELESALE_TRANSACTION_LOG_PATH}/transaction_log_%{DATA_DATE_YYYYMM}.csv`
  - Per-file metrics: `data_date`, `filename`, `status`, `latency_ms`, `token_usage`, `cost`
- Performance log (SharePoint): `${TELESALE_PERFORMANCE_LOG_PATH}/performance_log_%{DATA_DATE_YYYYMM}.csv`
  - Pipeline-level: `total_transaction`, `success_rate`, `total_runtime`

---

### 7. `TelesaleEvaluationOutputTask`

Generates an evaluation Excel report comparing AI predictions. Used in the evaluation pipeline only.

**Outputs:**
- Evaluation Excel (SharePoint): `evaluation/telesale_evaluation_%{DATA_DATE_YYYYMMDD}.xlsx` (sheet: "Evaluation")
- Evaluation JSON (SharePoint): same path as Excel with `.json` extension

---

### 8. `TelesaleFactCheckTask`

Standalone fact-checking pipeline that validates model predictions against a labelled ground truth dataset. Input data is validated using Pandera schemas (`FilenameListSchema`, `GroundTruthSchema`).

**Two-phase execution:**

| Phase | Trigger | Steps |
|---|---|---|
| **Submit** | No prediction files found in GCS | Load filename list → upload voices → map prompts → build JSONL → submit batch job |
| **Retrieve** | Prediction files exist in GCS | Download predictions → evaluate against ground truth → export evaluation log |

**Inputs:**
- Ground truth Excel (SharePoint): `${TELESALE_FACT_CHECK_PATH}/ground_truth/ground_truth_telesale_sentiment.xlsx` (sheet: "Evaluation")
- Filename list Excel (SharePoint): `${TELESALE_FACT_CHECK_PATH}/ground_truth/filename_list_fact_check.xlsx` (sheet: "FilenameList", cols: `filename`, `commission_skill_code`)
- Voice files (SharePoint): `${TELESALE_FACT_CHECK_PATH}/prediction/input/*.wav`

**Outputs:**
- GCS voice staging: `sentiment_telesale/fact_check/processing/voice/%{DATA_DATE_YYYYMMDDHHMMSS}/`
- GCS predictions: `sentiment_telesale/fact_check/output/%{DATA_DATE_YYYYMMDDHHMMSS}/predictions.jsonl`
- Evaluation log (SharePoint): `${TELESALE_FACT_CHECK_PATH}/prediction/output/%{DATA_DATE_YYYYMM}/fact_check_telesale_log_%{DATA_DATE_YYYYMMDD}.xlsx`

**Evaluation metrics per dimension (30+ fields):**

| Metric | Thresholds |
|---|---|
| Accuracy | acceptable ≥ 80%, good ≥ 85%, excellent ≥ 90% |
| Precision | acceptable ≥ 75%, good ≥ 80%, excellent ≥ 90% |
| Recall | acceptable ≥ 75%, good ≥ 80%, excellent ≥ 90% |
| F1-Score | acceptable ≥ 75%, good ≥ 80%, excellent ≥ 90% |

---

## Pandera Schemas

Located in `schemas/`. These schemas validate DataFrame inputs using [Pandera](https://pandera.readthedocs.io/):

| Schema | File | Purpose |
|---|---|---|
| `ControlLogSchema` | `control_log_schema.py` | Validates control log DataFrames (columns: `run_dt`, `datamonth`, `datadate`, `processed_status`, `remark`) |
| `FilenameListSchema` | `filename_list_schema.py` | Validates filename list for fact-check (columns: `filename`, `commission_skill_code`, `commission_skill`) |
| `GroundTruthSchema` | `ground_truth_schema.py` | Validates ground truth data with 30+ evaluation fields across all QA categories |
| `AgentMasterSchema` | `agent_master_schema.py` | Validates agent master Excel (columns: `emp_id`, `commission_skill_code`, `commission_skill`, `updatedate`) |
| `CheckListCategorySchema` | `check_list_schema.py` | Validates check list Categories sheet |
| `CheckListSchemaSubcategoriesSchema` | `check_list_schema.py` | Validates check list Subcategories sheet |
| `CheckListTagsSchema` | `check_list_schema.py` | Validates check list Tags sheet (includes `is_active` flag for dynamic tag filtering) |
| `Metadata` | `metadata.py` | Defines log schemas: `TRANSACTION_LOG_SCHEMA` (34 fields), `PERFORMANCE_LOG_SCHEMA` (18 fields), `BATCH_PROCESSING_LOG_SCHEMA` (19 fields), `GT_FIELD_MAPPING` (ground truth → dimension path mapping), and `EVALUATION_OUTPUT_SCHEMA` (20+ evaluation metric fields) |

---

## Output Validation Models

Located in `output_validation/`. Each campaign has its own Pydantic validation class and a corresponding `build_*_validation()` function that dynamically constrains `operation_check_list` to only active tag codes from the check list Excel. Campaign-to-model routing is handled by `ValidationMapping` in `output_validation/validate_mapping.py`.

| Campaign Key | Validation Class | File |
|---|---|---|
| `01_True_CVG_DIGITAL` | `CvgDigitalValidation` | `true_cvg_digital.py` |
| `03_True_CVG_Post` | `CvgPostValidation` | `true_cvg_pos.py` |
| `08_True_P2P_M1` | `P2PM1Validation` | `true_p2p_m1.py` |
| `09_True_P2P_M2` | `P2PM2Validation` | `true_p2p_m2.py` |
| `10_True_UTOL` | `UtolValidation` | `true_utol.py` |
| `13_True_Promo_End` | `PromoEndValidation` | `promo_end.py` |
| `Postpaid Upsell` | `PostpaidUpsellValidation` | `postpaid_upsell.py` |

All validation models share the same high-level field categories:

| Category | Key Fields |
|---|---|
| **Operations & Professionalism** | call opening, customer ID verification, language/tone, active listening, call closing |
| **Sales Effectiveness** | needs analysis, offer presentation, objection handling, closing, cross-sell/upsell |
| **Customer Experience** | positive experience, clarity, trust building |
| **Compliance** | data privacy, sales integrity, professional conduct |
| **Check List** | Campaign-specific binary flags (active tags from check list Excel) |
| **Support Detail** | Evidence-based reasoning with direct quotes (max 800 chars) |
| **Campaign Ratio** | Breakdown of main vs. other topics (must sum to 1.0) |
| **Sales Performance** | main package offered/accepted, upsell offered/accepted, cross-sell offered/accepted, product category lists |
| **Customer Insight** | rejection reason, network issue, churn risk indicator (0–100), customer sentiment (Positive/Neutral/Negative) |

---

## Adding a New Task

1. Create a file in `tasks/sentiment_telesale/`
2. Subclass `TaskInterface` and override `execute_task()`
3. Decorate with `@task_registry.register('YourTaskName')`
4. Import the class in `tasks/sentiment_telesale/__init__.py`
5. Add to the pipeline YAML config under the `tasks:` list

---

## How to deploy CI/CD
1. Push code to branch 'feature/sentiment-telesale'
2. (nprd) cloud build triggers automatically to deploy branch 'feature/sentiment-telesale' to nprd
3. Pull request and merge 'feature/sentiment-telesale' into 'develop' branch
4. Create new branch from 'main', use naming 'release/telesale-vx.x.x'
5. git checkout to 'release/telesale-vx.x.x'
6. select commit(s) that you want to push to production from 'develop' using cherry-pick (command: 'git cherry-pick {commit hash}')
7. push code to 'release/telesale-vx.x.x'
8. (release) In GitHub, create new tag 'telesale-vx.x.x-rcx' reference branch 'release/telesale-vx.x.x'
9. (release) cloud build triggers automatically to deploy tag 'telesale-vx.x.x-rcx' to release (but still nprd prject)
10. Pull request and merge 'release/telesale-vx.x.x' to 'main'
11. (prod) create new tag 'telesale-vx.x.x' reference branch 'main'
12. (prod) cloud build triggers automatically to deploy tag 'telesale-vx.x.x' to prod

---
