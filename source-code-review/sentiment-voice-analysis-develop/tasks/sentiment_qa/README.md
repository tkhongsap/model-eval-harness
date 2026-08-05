# Sentiment QA — Task Documentation

AI-powered call quality (QA) analysis pipeline built on the shared framework in `src/`. Processes voice recordings across multiple products from SharePoint through the Google Gemini Batch API and exports structured QA reports.

---

## Project QA Structure

```
sentiment-voice-analysis/
├── cloud_build/                                      # 🚀 CI/CD Pipelines
│   ├── common/                                       #    Shared utilities (tf_unlock)
│   └── sentiment_qa/                                 #    QA Dockerfile & deployment
│
├── config/                                           # ⚙️ YAML Configurations
│   ├── common.yml                                    #    Shared config
│   └── sentiment_qa/                                 #    QA pipeline configs
│       ├── qa_pipeline_fact_check.yml                #    Orchestrate fact check
│       ├── qa_pipeline_tasks.yml                     #    Orchestrate main process
│       ├── qa_pipeline_user_playground.yml           #    Orchestrate user playground
│       └── system_prompt/
│           ├── SYSTEM_PROMPT_GUIDE.md                #    IGNORE THIS FILE
│           ├── system_prompt.txt                     #    System prompt
│           └── user_config.xlsx                      #    User config
│
├── resources/                                        # 📄 static files
│
├── src/                                              # 🏗️ Framework & Shared Code
│   ├── core/                                         #    Engine, TaskInterface, TaskRegistry
│   ├── modules/                                      #    Integrations (GCS, SharePoint, Gemini)
│   └── utils/                                        #    Logger, date utils, env resolver
│
├── tasks/                                            # 📋 Project Task Implementations
│   └── sentiment_qa/                                 #    QA pipeline tasks
│       ├── get_batch_result_task.py                  #    1st task
│       ├── export_output_result_task.py              #    2nd task
│       ├── upload_voice_task.py                      #    3rd task
│       ├── prep_payload_task.py                      #    4th task
│       ├── execute_batch_job_task.py                 #    5th task
│       ├── fact_check_task.py                        #    Fact check task
│       └── user_playground_task.py                   #    User playground task
│
├── terraform/                                        # 🏗️ Infrastructure as Code
│   ├── modules/                                      #    Reusable modules (Cloud Run, GCS, etc.)
│   └── projects/                                     #    Project-specific infra configs
│       └── sentiment_qa/
│
├── tests/                                            # 🧪 Unit & integration tests
│   └── test_tasks/
│       └── sentiment_qa/
│           ├── test_get_batch_result_task.py         #    1st task test
│           ├── test_export_output_result_task.py     #    2nd task test
│           ├── test_upload_voice_task.py             #    3rd task test
│           ├── test_prep_payload_task.py             #    4th task test
│           ├── test_execute_batch_job_task.py        #    5th task test
│           ├── test_fact_check_task.py               #    Fact check task test
│           └── test_user_playground_task.py          #    Fact check task test
│
├── main.py                                           # 🎯 Entry point with CLI args
├── pyproject.toml                                    # 📦 Dependencies (UV)
├── uv.lock                                           # 🔒 UV lock file
└── .env.example                                      # 📝 Environment template
```

---

## Pipeline Variants

Three pipeline configs are available in `config/sentiment_qa/`:

| Config File                        | Purpose                                                                          |
| ---------------------------------- | -------------------------------------------------------------------------------- |
| `qa_pipeline_tasks.yml`            | **Main pipeline** — daily production run                                         |
| `qa_pipeline_fact_check.yml`       | **Fact-check pipeline** — standalone validation against ground truth             |
| `qa_pipeline_user_playground.yml`  | **User playground pipeline** — standalone user playground for testing new prompt |

Run a pipeline:

```bash
uv run python main.py --config_path config/sentiment_qa/qa_pipeline_tasks.yml
uv run python main.py --config_path config/sentiment_qa/qa_pipeline_fact_check.yml
uv run python main.py --config_path config/sentiment_qa/qa_pipeline_user_playground.yml
```

---

## Task Reference

| # | Registry Name                 | File                          | Pipeline        | Purpose |
|---|-------------------------------|-------------------------------|-----------------|---------|
| 1 | `QAGetBatchResultTask`        | `get_batch_result_task.py`    | Main            | Discover predictions in GCS for the previous day's batch and parse them |
| 2 | `QAExportOutputResultTask`    | `export_output_result_task.py`| Main            | Score predictions against `user_config.xlsx`, build the QA report Excel, archive GCS files, write audit logs |
| 3 | `QAUploadVoiceTask`           | `upload_voice_task.py`        | Main            | Download voice files from SharePoint per product, upload to GCS, send summary email |
| 4 | `QAPrepPayloadTask`           | `prep_payload_task.py`        | Main            | Build JSONL batch payloads using the system prompt + per-product `user_config.xlsx` weights |
| 5 | `QAExecuteBatchJobTask`       | `execute_batch_job_task.py`   | Main            | Submit the JSONL batch job to Vertex AI |
| 6 | `QAFactCheckTask`             | `fact_check_task.py`          | Fact-check      | Two-phase ground-truth validation (submit batch / retrieve & evaluate) |
| 7 | `QAUserPlaygroundTask`        | `user_playground_task.py`     | User playground | User playground for testing new prompt |


The main pipeline runs the prior-batch retrieval (tasks 1–2) before submitting new batch (tasks 3–5) in the same execution.

---

## Task Details

### 1. `QAGetBatchResultTask`

Lists `predictions.jsonl` files under the configured `output_folder` for the lookback window, downloads each batch, and parses prediction lines into structured records.

**Inputs:**
- GCS predictions: `sentiment_qa/output/%{DATA_DATE_YYYYMM}/%{DATA_DATE_YYYYMMDD}/**/predictions.jsonl`

**Output passed downstream:**
```python
{
  "list_batchs": [...],     # GCS paths of prediction JSONL files
  "batch_results": [...],   # parsed prediction records (file_metadata + prediction)
  "failed_batches": [...]   # batches that failed to download / parse
}
```

---

### 2. `QAExportOutputResultTask`

Calculates per-criterion scores using weights from `user_config.xlsx`, writes the consolidated Excel report to SharePoint Verint, archives GCS files, and writes Transaction / Performance / Batch-processing logs.

**Outputs:**
- Master Excel (SharePoint Verint): `${QA_VERINT_ROOT}/${QA_VERINT_OUTPUT}/%{DATA_DATE_YYYYMM}/master.xlsx`
- Daily Excel (SharePoint Verint): `${QA_VERINT_ROOT}/${QA_VERINT_OUTPUT}/%{DATA_DATE_YYYYMM}/%{DATA_DATE_YYYYMMDD}/Service Quality Report by Call_Department_%{DATA_DATE_YYYYMMDD}.xlsx`
- Daily Excel (SharePoint AI-Automation): `sentiment_reason_addon_network/Output/QA/%{DATA_DATE_YYYYMM}/Result_Sentiment_Analysis_QA_%{DATA_DATE_YYYYMMDD}.xlsx`
- Archived voice (GCS): `sentiment_qa/archive/voice/%{DATA_DATE_YYYYMM}/%{DATA_DATE_YYYYMMDD}/{PRODUCT}`
- Archived batch (GCS): `sentiment_qa/archive/batch/`
- Transaction log (SharePoint): `${QA_TRANSACTION_LOG_PATH}/transaction_log_%{DATA_DATE_YYYYMM}.csv`
- Performance log (SharePoint): `${QA_PERFORMANCE_LOG_PATH}/performance_log_%{DATA_DATE_YYYYMM}.csv`

---

### 3. `QAUploadVoiceTask`

Downloads voice files from SharePoint Verint for **each product** in the combined list of `QA_VERINT_PRODUCTS_INBOUND` and `QA_VERINT_PRODUCTS_OUTBOUND` and uploads them to GCS. Maintains a control log to skip already-processed dates and sends a summary email via Microsoft Graph.

**Inputs:**
- SharePoint Verint: `${QA_VERINT_ROOT}/Input/${QA_VERINT_PRODUCTS}/%{DATA_DATE_YYYYMM}/%{DATA_DATE_YYYYMMDD}/` (the `${QA_VERINT_PRODUCTS}` segment is substituted per-product from the inbound/outbound lists, not a standalone env var)

**Outputs:**
- GCS: `sentiment_qa/input/%{DATA_DATE_YYYYMM}/%{DATA_DATE_YYYYMMDD}/{PRODUCT}/*.wav`
- Control log (SharePoint): `${QA_CONTROL_FILE_PATH}/control_file.xlsx`
- Notification email to `${USER_EMAIL}`, cc `${OPER_EMAIL}, ${DEV_EMAIL}`

---

### 4. `QAPrepPayloadTask`

Combines the system prompt with the per-product weights/criteria from `user_config.xlsx` and builds the Gemini Batch JSONL payload for every voice file in the GCS input folder.

**Inputs:**
- `config/sentiment_qa/system_prompt/system_prompt.txt`
- `config/sentiment_qa/system_prompt/user_config.xlsx` (local copy of the per-product weights/criteria)
- SharePoint copy: `${QA_USER_PROMPT_PATH}/user_config.xlsx`
- GCS voice files: `sentiment_qa/input/%{DATA_DATE_YYYYMM}/%{DATA_DATE_YYYYMMDD}/`

**Outputs:**
- GCS staged voices: `sentiment_qa/processing/voice/%{DATA_DATE_YYYYMM}/%{DATA_DATE_YYYYMMDD}/`
- GCS JSONL payload: `sentiment_qa/processing/batch/%{DATA_DATE_YYYYMM}/%{DATA_DATE_YYYYMMDD}/%{DATA_DATE_YYYYMMDDHHMMSS}/payloads.jsonl`
- Backup of the prompt Excel used: `sentiment_qa/archive/prompt/%{DATA_DATE_YYYYMMDD}/user_config.xlsx`

---

### 5. `QAExecuteBatchJobTask`

Submits the JSONL payload to the Vertex AI Gemini Batch API and polls its initial status. Writes batch metadata to the SharePoint batch-processing log.

**Outputs:**
- Vertex AI batch job (display name template `sentiment-qa-batch-job-%{DATA_DATE_YYYYMMDDHHMMSS}`)
- Batch processing log (SharePoint): `${QA_BATCH_PROCESSING_LOG_PATH}/batch_processing_log.csv`

---

### 6. `QAFactCheckTask`

Standalone fact-checking pipeline that validates model predictions against a labelled ground-truth Excel. Like the telesale variant it is two-phase: when no predictions exist in GCS it submits a batch; otherwise it downloads predictions and writes an evaluation report.

**Inputs:**
- Ground truth Excel (SharePoint): `${QA_FACT_CHECK_PATH}/ground_truth/ground_truth_qa_sentiment.xlsx`
- Voice files (SharePoint), one subfolder per product in `QA_FACT_CHECK_PRODUCTS`: `${QA_FACT_CHECK_PATH}/prediction/input/{product}`
- Per-product weights: `${QA_USER_PROMPT_PATH}/user_config.xlsx`

**Outputs:**
- GCS voice staging: `sentiment_qa/fact_check/processing/voice/%{DATA_DATE_YYYYMMDDHHMMSS}/`
- GCS predictions: `sentiment_qa/fact_check/output/%{DATA_DATE_YYYYMMDDHHMMSS}/predictions.jsonl`
- Evaluation log (SharePoint): `${QA_FACT_CHECK_PATH}/prediction/output/%{DATA_DATE_YYYYMM}/fact_check_qa_log_%{DATA_DATE_YYYYMMDD}.xlsx`

**Evaluation metric thresholds** (from `qa_pipeline_fact_check.yml`):

| Metric    | acceptable | good | excellent |
|-----------|------------|------|-----------|
| Accuracy  | 80         | 85   | 90        |
| Precision | 75         | 80   | 90        |
| Recall    | 75         | 80   | 90        |
| F1-Score  | 80         | 85   | 90        |

---

### 7. `QAUserPlaygroundTask`

Standalone on-demand pipeline used by operators to score an ad-hoc set of voice files dropped into a SharePoint Control folder with a custom `user_config.xlsx`. Runs end-to-end in a single execution (upload → batch → score → export → archive) rather than being split across two days like the main pipeline. Sends a result-summary email via Microsoft Graph when complete.

**Inputs:**
- Source voices (SharePoint Control): `${QA_CONTROL_ROOT}/${QA_USER_PLAYGROUND_PATH}/input/`
- Per-run weights (SharePoint Control): `${QA_CONTROL_ROOT}/${QA_USER_PLAYGROUND_PATH}/user_config.xlsx`

**Outputs:**
- GCS voice staging: `sentiment_qa/user_playground/processing/voice/%{DATA_DATE_YYYYMMDDHHMMSS}/`
- GCS predictions: `sentiment_qa/user_playground/output/%{DATA_DATE_YYYYMMDDHHMMSS}/`
- Daily Excel (SharePoint Control): `${QA_CONTROL_ROOT}/${QA_USER_PLAYGROUND_PATH}/output/result_%{DATA_DATE_YYYYMMDD}_%{DATA_DATE_HHMMSS}.xlsx`
- Source-folder archive (SharePoint Control): `${QA_CONTROL_ROOT}/${QA_USER_PLAYGROUND_PATH}/archive/%{DATA_DATE_YYYYMMDDHHMMSS}/`
- Backup of prompt Excel (GCS): `sentiment_qa/user_playground/archive/prompt/%{DATA_DATE_YYYYMMDD}/user_config.xlsx`
- Notification email to `${USER_EMAIL}`, cc `${OPER_EMAIL}, ${DEV_EMAIL}`

---

## Pandera Schemas

Located in `schemas/`:

| Schema             | File                       | Purpose |
|--------------------|----------------------------|---------|
| `GroundTruthSchema`| `ground_truth_schema.py`   | Validates the QA ground-truth Excel used by `QAFactCheckTask` (per-criterion `*_keyword_or_detail` columns across customer interaction, compliance, problem handling, service ownership, and customer experience categories) |

---

## Adding a New Task

1. Create a file in `tasks/sentiment_qa/`
2. Subclass `TaskInterface` and override `execute_task()`
3. Decorate with `@task_registry.register('YourTaskName')`
4. Import the class in `tasks/sentiment_qa/__init__.py`
5. Add to the pipeline YAML config under the `tasks:` list

---

## How to deploy CI/CD
1. Push code to branch 'feature/sentiment-qa'
2. (nprd) cloud build triggers automatically to deploy branch 'feature/sentiment-qa' to nprd
3. Pull request and merge 'feature/sentiment-qa' into 'develop' branch
4. Create new branch from 'main', use naming 'release/qa-vx.x.x'
5. git checkout to 'release/qa-vx.x.x'
6. select commit(s) that you want to push to production from 'develop' using cherry-pick (command: 'git cherry-pick {commit hash}')
7. push code to 'release/qa-vx.x.x'
8. (release) In GitHub, create new tag 'qa-vx.x.x-rcx' reference branch 'release/qa-vx.x.x'
9. (release) cloud build triggers automatically to deploy tag 'qa-vx.x.x-rcx' to release (but still nprd prject)
10. Pull request and merge 'release/qa-vx.x.x' to 'main'
11. (prod) create new tag 'qa-vx.x.x' reference branch 'main'
12. (prod) cloud build triggers automatically to deploy tag 'qa-vx.x.x' to prod

---

## When AI output change
Edit these 2 code to align with the new output schema
1. config/sentiment_qa/system_prompt/system_prompt.txt
2. tasks/sentiment_qa/prep_payload_task.py > function _get_analysis_schema()

---

## Batch Automation with Eventarc + Workflows

Each batch run uses Eventarc and Workflows for automation. After batch processing completes and output files are written, Eventarc triggers Workflows, and Workflows then trigger Cloud Run to automatically collect the results.

### Limitations and Design Decisions

1. Eventarc is configured with a `storage.objects.create` trigger.
  - Limitation: when output files are large, GCS can emit multiple events, which may cause duplicate triggers.
  - Mitigation in Workflows: before triggering Cloud Run, Workflows checks a control state file in GCS to verify whether the current day has already been triggered.
  - If the current day has not been triggered yet, Workflows sleeps for 120 seconds to ensure large output files are fully written before the next collection step starts then invokes Cloud Run.

2. Reason for not using `google.cloud.storage.object.v1.finalized`.
  - In the current setup, it cannot be constrained to the required monitored path/prefix.
  - As a result, it would trigger on every finalized object in the bucket, causing unnecessary workflow executions.
