# Sentiment Voice Analysis

## Overview

A **modular batch processing framework** for analyzing customer service call quality using **Google Gemini AI**. The framework processes audio recordings, performs comprehensive QA analysis via Vertex AI Batch API, and generates structured reports.

### Key Capabilities

- 📞 **Audio Processing**: SharePoint → GCS → Gemini Batch API
- 🤖 **AI Analysis**: Service quality, sales opportunities, customer sentiment & experience
- 📊 **Reporting**: Structured Excel output with detailed evaluations
- ☁️ **Cloud Native**: Runs on GCP Cloud Run Jobs with automated scheduling

---

## Repository Structure

```
sentiment-voice-analysis/
├── main.py                         # 🎯 Entry point with CLI args
├── pyproject.toml                  # 📦 Dependencies (UV)
├── uv.lock                        # 🔒 UV lock file
├── src/                            # 🏗️ Framework & Shared Code
│   ├── core/                       #    Engine, TaskInterface, TaskRegistry
│   ├── modules/                    #    Integrations (GCS, SharePoint, Gemini)
│   └── utils/                      #    Logger, date utils, env resolver
│
├── tasks/                          # 📋 Project Task Implementations
│   ├── sentiment_telesale/         #    Telesale pipeline tasks
│   ├── sentiment_qa/               #    QA pipeline tasks
│   ├── ocr_tax_invoice_pipeline/   #    Generic OCR batch pipeline (submit/retrieve/finalize)
│   └── tax_invoice_reconcile/      #    Tax-invoice reconcile business tasks (precheck/reconcile/reject)
│
├── config/                         # ⚙️ YAML Configurations
│   ├── common.yml                  #    Shared config
│   ├── sentiment_telesale/         #    Telesale pipeline configs
│   │   ├── resources/              #    Prompt files & check list Excel
│   │   └── system_prompt/          #    System prompt template
│   ├── sentiment_qa/               #    QA pipeline configs
│   └── tax_invoice_extraction/     #    Tax invoice extraction configs
│
├── resources/                      # 📄 static files
│
├── cloud_build/                    # 🚀 CI/CD Pipelines
│   ├── common/                     #    Shared utilities (tf_unlock)
│   ├── sentiment_telesale/         #    Telesale Dockerfile & deployment
│   │   └── .env.example            #    Project-specific env template
│   ├── sentiment_qa/               #    QA Dockerfile & deployment
│   └── tax_invoice_extraction/     #    Tax-invoice Dockerfile & deployment
│
├── terraform/                      # 🏗️ Infrastructure as Code
│   ├── modules/                    #    Reusable modules (Cloud Run, GCS, etc.)
│   └── projects/                   #    Project-specific infra configs
│       ├── sentiment_telesale/
│       ├── sentiment_qa/
│       └── tax_invoice_extraction/
│
└── tests/                          # 🧪 Unit & integration tests
```

---

## Framework Architecture

```
main.py → CoreEngine → TaskRegistry → Task Execution
                                      ├── Task 1
                                      ├── Task 2
                                      └── Task N

Task Lifecycle: validate() → pre_execute() → execute_task() → post_execute() → cleanup()
```

### Core Components

| Component         | Purpose                           | Key Methods                                                  |
| ----------------- | --------------------------------- | ------------------------------------------------------------ |
| **CoreEngine**    | Orchestrates task execution       | `run()`, `_load_config()`                                    |
| **TaskInterface** | Abstract base class for tasks     | `validate()`, `pre_execute()`, `execute_task()`, `cleanup()` |
| **TaskRegistry**  | Decorator-based task registration | `@task_registry.register("TaskName")`                        |

### Integration Modules

| Module                | Purpose              | Key Operations                                         |
| --------------------- | -------------------- | ------------------------------------------------------ |
| **GCSModule**         | Google Cloud Storage | Upload/download files, list blobs, async operations    |
| **GeminiBatchModule** | Gemini Batch API     | Create/monitor/cancel batch jobs, handle job states    |
| **SharePointModule**  | Microsoft Graph API  | MSAL auth, file upload/download, retry logic (423/409) |

### Utility Functions

- **`resolve_env(text)`**: Resolve `${ENV_VAR}` placeholders in YAML configs
- **`resolve_date(text, date)`**: Resolve `%{DATA_DATE_YYYYMMDD}` with offsets (±7D, -1M)
- **Logger**: JSON format (prod) or dev format (local), timezone-aware (Asia/Bangkok)

---

## 🚀 Local Development Setup

### Prerequisites

- **Python 3.11+**
- **Google Cloud CLI** (`gcloud`)
- **UV** (recommended for dependency management)

### Quick Start

#### 1. Install UV

```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Linux/Mac
curl -LsSf https://astral.sh/uv/install.sh | sh

# Verify installation
uv --version
```

#### 2. Authenticate with Google Cloud

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project <YOUR_PROJECT_ID>
gcloud auth application-default set-quota-project <YOUR_PROJECT_ID>
```

#### 3. Setup Python Environment

```bash
# Install dependencies (UV automatically creates venv)
uv sync

# OR manually with venv + pip:
# python -m venv .venv
# .venv\Scripts\Activate.ps1  # Windows
# source .venv/bin/activate    # Linux/Mac
# pip install -r requirements.txt
```

#### 4. Configure Environment

Environment templates are provided per project:

| File                                          | Purpose                                        |
| --------------------------------------------- | ---------------------------------------------- |
| `cloud_build/sentiment_telesale/.env.example` | Variables for the telesale pipeline             |
| `cloud_build/sentiment_qa/.env.example`       | Variables for the QA pipeline                   |

```bash
# Copy the relevant template for local development
cp cloud_build/sentiment_telesale/.env.example .env

# Edit .env with your credentials
```

#### 5. Run the Application

```bash
# Default run
uv run python main.py --config_path <path-to-config.yml>

# Reprocess specific date
uv run python main.py --config_path <path-to-config.yml> --rerun_data_dt 2026-02-13

# Enable debug logging
LOG_LEVEL=DEBUG uv run python main.py --config_path <path-to-config.yml>
```

### Command Line Arguments

| Argument          | Short | Description                                          | Default  |
| ----------------- | ----- | ---------------------------------------------------- | -------- |
| `--config_path`   | `-c`  | Path to YAML config file                             | Required |
| `--rerun_data_dt` | `-r`  | Reprocess a single date (YYYY-MM-DD)                 | None     |
| `--start_data_dt` | `-s`  | Start of a date range (YYYY-MM-DD)                   | None     |
| `--end_data_dt`   | `-e`  | End of a date range (YYYY-MM-DD)                     | None     |

### Running Tests

```bash
# Run all tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=src --cov=tasks --cov-report=term-missing

# Run specific test file
uv run pytest tests/<test_file>.py
```

### Verify Setup

```bash
# Check Python version
python --version  # Should be 3.11+

# Check gcloud authentication
gcloud auth list

# Verify imports
uv run python -c "from src.core.engine import CoreEngine; print('✓ Imports successful')"
```

---

## Configuration System

### YAML Configuration

Task pipelines are configured via YAML files in `config/`. Configuration supports:

- **Dynamic Environment Placeholders**: `${ENV_VAR}` — resolved from environment variables
- **Dynamic Date Placeholders**: `%{DATA_DATE_YYYYMMDD}` — auto-replaced with current/specified date
- **Date Offsets**: `%{DATA_DATE-7D_YYYYMMDD}` (7 days ago), `%{DATA_DATE-1M_YYYYMMDD}` (1 month ago)

### Environment Variables (`.env`)

Copy `.env.example` to `.env` and configure project-specific variables. See each project's README for required variables.

---

## Technology Stack

**Core**: Python 3.11, Pydantic, Pandera, Pandas, DuckDB, asyncio
**AI**: Google Gemini 2.5 Flash, Vertex AI Batch API (`google-genai`)
**OCR**: pypdfium2 (PDF raster), OpenCV + Pillow (image quality scoring)
**Storage**: GCS, SharePoint Online
**Auth**: Google ADC, Microsoft MSAL (OAuth2)
**Deployment**: Docker, Cloud Run Jobs, Cloud Scheduler
**IaC**: Terraform (Google Provider 7.20.0)
**Format**: JSONL (batch I/O), Excel (reports), JSON (logging)

---

## Projects

| Project                    | Config Path                       | Description                                       | Pipelines                                                            | Docs                                                                                                                   |
| -------------------------- | --------------------------------- | ------------------------------------------------- | -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| **Sentiment Telesale**     | `config/sentiment_telesale/`      | QA analysis for telesale call recordings          | `telesale_pipeline_tasks.yml`, `_evaluate.yml`, `_fact_check.yml`    | [Tasks](tasks/sentiment_telesale/README.md) · [Cloud Build](cloud_build/sentiment_telesale/README.md) · [Terraform](terraform/projects/sentiment_telesale/README.md) |
| **Sentiment QA**           | `config/sentiment_qa/`            | QA analysis for general call recordings           | `qa_pipeline_tasks.yml`, `qa_pipeline_fact_check.yml`, `qa_pipeline_user_playground.yml` | [Tasks](tasks/sentiment_qa/README.md) · [Cloud Build](cloud_build/sentiment_qa/README.md) · [Terraform](terraform/projects/sentiment_qa/README.md) |
| **OCR Tax Invoice**        | `config/tax_invoice_extraction/`  | OCR extraction from tax-invoice PDFs/images, then reconciliation against Master Buyer / Master Vendor / Z45 reports  | `ocr_pipeline_pre_tasks.yml` (submit), `ocr_pipeline_post_tasks.yml` (retrieve + reconcile + finalize) | [OCR Pipeline](tasks/ocr_tax_invoice_pipeline/README.md) · [Reconcile](tasks/tax_invoice_reconcile/README.md) · [Config](config/tax_invoice_extraction/README.md) · [Cloud Build](cloud_build/tax_invoice_extraction/README.md) · [Terraform](terraform/projects/tax_invoice_extraction/README.md) |

---

## Troubleshooting

| Issue                              | Solution                                                   |
| ---------------------------------- | ---------------------------------------------------------- |
| **Authentication Error**           | Run `gcloud auth application-default login`                |
| **SharePoint 423 (Locked)**        | Auto-retries 3x with 10s delay                             |
| **Batch Job Failed**               | Check JSONL format, GCS URIs, audio formats (wav/mp3/flac) |
| **Missing Env Vars**               | Verify `.env` file exists and is loaded                    |
| **Permission Denied (GCS)**        | Check SA has `Storage Object Admin` role                   |
| **Permission Denied (SharePoint)** | Verify Azure AD app has `Sites.ReadWrite.All` permission   |

---

## Support

**Team**: RPA & AI Automation
**Project**: Sentiment Voice Analysis
**Last Updated**: July 2026
