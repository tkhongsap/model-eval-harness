# Sentiment Batch MNP Retention

## Project Overview
This project is an automated batch processing pipeline designed to analyze customer retention calls using Generative AI. It leverages Google Gemini models to process audio recordings of customer service interactions.

The system performs the following key functions:
- **Audio Processing**: Ingests customer call recordings from Google Cloud Storage.
- **GenAI Analysis**: Utilizes Google's Gemini models to transcribe and analyze conversation content in Thai.
- **Sentiment & Churn Analysis**:
  - Identifies **Cancellation Reasons** (Main, Secondary, Third) such as cost, network quality, or promotion expiry.
  - Determines the **Call Result** (e.g., Retention, Downsell).
  - Detects influencing events and provides recommendations.
- **Reporting**: Generates structured JSON outputs and integrates with SharePoint for data reporting.

## Directory Structure
- `src/`: Core application source code.
  - `main.py`: Main entry point for the batch processing pipeline.
  - `modules/`: specific logic modules key functionalities (GCS, SharePoint, Fact Checker).
  - `data_model.py`: Validation schemas and data structures.
- `config/`: Configuration files for system prompts and model settings.
- `cloud_build/`: CI/CD workflow configurations.
- `fonts/`: Font resources.

## Local Development Setup

### Prerequisites
- Python 3.11+
- Google Cloud CLI (`gcloud`)

### Steps to run locally

1. **Login to Google Cloud**
   ```bash
   gcloud auth login
   # OR
   gcloud auth application-default login
   ```

2. **Set Active Project**
   ```bash
   gcloud config set project <PROJECT_ID>
   gcloud auth application-default set-quota-project <PROJECT_ID>
   ```

3. **Create and Activate Virtual Environment**
   ```bash
   # Create venv
   python -m venv .venv

   # Activate (Windows PowerShell)
   .venv\Scripts\Activate.ps1
   # Activate (Linux/Mac)
   # source .venv/bin/activate
   ```

4. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

5. **Configure Environment Variables**
   - Copy `.env.example` to `.env`
   - Fill in the required variables in `.env`

6. **Run the Application**
   ```bash
   python -m src.main
   ```

---

### To manual deploy to GCP
1. deploy to non-prod <br>
    1.1. `gcloud config set project <PROJECT_ID>` <br>
    1.2. `gcloud builds submit --config=./deployment/nonprod.yaml .`
2. deploy to prod <br>
    2.1. `gcloud config set project <PROJECT_ID>` <br>
    2.2. `gcloud builds submit --config=./deployment/prod.yaml .` <br>
    if 2.2. encounter invalid permission <br>
        2.3. manually create cloud run job (select docker image from `artifact registry` in `gcp-noexp-wl-nprd-sentiment`) <br>
        2.4. remember to assign secret value from `secret manager` in `gcp-noexp-wl-prod-sentiment` <br>
        2.5. create cloud scheduler to trigger cloud run job <br>

## Example input.json
```json
{
  "request": {
    "contents": [
      {
        "role": "user",
        "parts": [
          {
            "text": "{{YOU PROMPT}}"
          },
          {
            "fileData": {
              "fileUri": "gs://setiment-batch-setiment-batch-store/processing/202511/20251107/9156324873950003681_0956583395_085859_50003623__Surachet_Virunsap_T.wav",
              "mimeType": "audio/wav"
            }
          }
        ]
      }
    ],
    "generationConfig": {
      "temperature": 0.1,
      "topP": 1,
      "maxOutputTokens": 65000
    }
  }
}
```

## Example output.json
```json
{
  "request": {
    "contents": [
      {
        "parts": [
          {
            "fileData": null,
            "text": "{{YOU PROMPT}}"
          },
          {
            "fileData": {
              "fileUri": "gs://setiment-batch-setiment-batch-store/processing/202511/20251107/9156324873950003681_0956583395_085859_50003623__Surachet_Virunsap_T.wav",
              "mimeType": "audio/wav"
            },
            "text": null
          }
        ],
        "role": "user"
      }
    ],
    "generationConfig": {
      "maxOutputTokens": 65000,
      "temperature": 0.1,
      "topP": 1
    }
  },
  "status": "",
  "response": {
    "candidates": [
      {
        "avgLogprobs": -1.8023319529063666,
        "content": {
          "parts": [
            {
              "text": "```json\n{\n  \"reason\": {\n    \"main\": \"ลูกค้าใช้งานยี่หัส\",\n    \"secondary\": \"คีืนค้าี WiFi\",\n    \"third\": \"ลลนาโช่รามย อัทสารางานออข\"\n  },\n  \"keyword\": \"ป่วย, อยู่บ้าน, ใช้ WiFi ที่บ้าน, อยากประหยัด\",\n  \"call_result\": \"Downsell\",\n  \"call_event_detection\": \"Campaign-Driven Events\",\n  \"trigger_event_to_contact\": \"Downgrade\"\n}\n```"
            }
          ],
          "role": "model"
        },
        "finishReason": "STOP",
        "score": -241.51248168945312
      }
    ],
    "createTime": "2025-11-07T10:24:09.112444Z",
    "modelVersion": "gemini-2.5-flash",
    "responseId": "ycgNabzuBo6YmPUP1ZvmqAs",
    "usageMetadata": {
      "billablePromptUsage": {
        "audioDurationSeconds": 377,
        "textCount": 3629
      },
      "candidatesTokenCount": 134,
      "candidatesTokensDetails": [
        {
          "modality": "TEXT",
          "tokenCount": 134
        }
      ],
      "promptTokenCount": 11222,
      "promptTokensDetails": [
        {
          "modality": "AUDIO",
          "tokenCount": 9400
        },
        {
          "modality": "TEXT",
          "tokenCount": 1822
        }
      ],
      "thoughtsTokenCount": 1759,
      "totalTokenCount": 13115,
      "trafficType": "ON_DEMAND"
    }
  },
  "processed_time": "2025-11-07T10:24:52.764602+00:00"
}
```

---

## Future Deployment Option: GitHub Actions with Workload Identity Federation

> **Note:** Currently, we use the manual `gcloud builds submit` method (see "To manual deploy to GCP" above). The following instructions are for setting up a future automated CI/CD pipeline using GitHub Actions and Workload Identity Federation.

### Workflow Overview
1. **Nonprod**: Builds the Docker image using Cloud Build, pushes to Artifact Registry, and deploys to Cloud Run Jobs.
2. **Prod**: Promotes the existing image from `nonprod` (does not rebuild) and deploys to Cloud Run Jobs.

## Infrastructure Setup (Future)

Follow these steps to set up the connection between GitHub Actions and Google Cloud Platform using Workload Identity Federation (Keyless Authentication).

### 1. Install gcloud CLI
### 2. Create Service Accounts
  - Deployer service account for Github Action deploy into GCP
  - Runner service account for trigger Cloud Run Job
  - Builder service account for Cloud Build to build and push image to Artifact Registry
### 3. Create GCS bucket 
  - For Nonprod
    - Cloud Build Source Staging Bucket: for staging source code during Cloud Build
    - Cloud Build Logs Bucket: for storing Cloud Build logs
  - For Prod
    - Cloud Build Logs Bucket: for storing Cloud Build logs
```bash
gcloud storage buckets create gs://{BUCKET_NAME} \
  --project={PROJECT_ID} \
  --location={REGION} \
  --default-storage-class=STANDARD \
  --uniform-bucket-level-access \
  --public-access-prevention
```
### 4. Assign roles to service accounts

#### Role Descriptions
| Role | Description |
| :--- | :--- |
| `Service Usage Consumer` | Allows the service account to consume project quota and verify enabled APIs. |
| `Cloud Build Editor` | Allows creating and managing builds. |
| `Storage Object Admin` | Full control over objects in GCS buckets (Read/Write/Delete). |
| `Cloud Run Developer` | Allows creating and managing Cloud Run services and jobs. |
| `Cloud Scheduler Admin` | Full control over Cloud Scheduler jobs. |
| `Service Account User` | Allows a service account to "act as" (impersonate) another service account. |
| `Artifact Registry Writer` | Allows pushing images to Artifact Registry. |
| `Logs Writer` | Allows writing logs to Cloud Logging. |
| `Storage Object Viewer` | Allows viewing and downloading objects in GCS buckets. |
| `Storage Object Creator` | Allows creating new objects in GCS buckets (Write-only). |
| `Storage Legacy Bucket Reader` | Allows reading bucket metadata (required for some log validations). |
| `Cloud Run Invoker` | Allows triggering/invoking Cloud Run services or jobs. |
| `Secret Manager Secret Accessor` | Allows accessing secret values from Secret Manager. |
| `Vertex AI User` | Allows using Vertex AI services and models. |
| `Artifact Registry Reader` | Allows reading images from Artifact Registry. (For Production Service Account) |

  **4.1. Deployer service account**
  - Service Usage Consumer
  - Cloud Build Editor
  - Storage Object Admin (Bucket level)
  - Storage Object Viewer (Bucket level)
  - Cloud Run Developer
  - Cloud Scheduler Admin
  - Service Account User (on Builder & Runner SAs)
  - Artifact Registry Reader
  - Storage Legacy Bucket Reader (Bucket level)
  ```bash
  gcloud projects add-iam-policy-binding {PROJECT_ID} \
    --member="serviceAccount:{DEPLOYER_SA_EMAIL}" \
    --role="roles/serviceusage.serviceUsageConsumer"

  gcloud projects add-iam-policy-binding {PROJECT_ID} \
    --member="serviceAccount:{DEPLOYER_SA_EMAIL}" \
    --role="roles/cloudbuild.builds.editor"

  gcloud projects add-iam-policy-binding {PROJECT_ID} \
    --member="serviceAccount:{DEPLOYER_SA_EMAIL}" \
    --role="roles/run.developer"

  gcloud projects add-iam-policy-binding {PROJECT_ID} \
    --member="serviceAccount:{DEPLOYER_SA_EMAIL}" \
    --role="roles/cloudscheduler.admin"

  gcloud projects add-iam-policy-binding {PROJECT_ID} \
    --member="serviceAccount:{DEPLOYER_SA_EMAIL}" \
    --role="roles/artifactregistry.reader"

  gcloud iam service-accounts add-iam-policy-binding {BUILDER_SA_EMAIL} \
    --member="serviceAccount:{DEPLOYER_SA_EMAIL}" \
    --role="roles/iam.serviceAccountUser"

  gcloud iam service-accounts add-iam-policy-binding {RUNNER_SA_EMAIL} \
    --member="serviceAccount:{DEPLOYER_SA_EMAIL}" \
    --role="roles/iam.serviceAccountUser"

  ## For Nonprod Deployer SA to read Source Staging Bucket
  gcloud storage buckets add-iam-policy-binding gs://{SOURCE_STAGING_BUCKET} \
    --member="serviceAccount:{DEPLOYER_SA_EMAIL}" \
    --role="roles/storage.objectAdmin"

  ## For Nonprod Deployer SA to read and validate source staging bucket
  gcloud storage buckets add-iam-policy-binding gs://{SOURCE_STAGING_BUCKET} \
    --member="serviceAccount:{DEPLOYER_SA_EMAIL}" \
    --role="roles/storage.legacyBucketReader"

  gcloud storage buckets add-iam-policy-binding gs://{LOGS_BUCKET} \
    --member="serviceAccount:{DEPLOYER_SA_EMAIL}" \
    --role="roles/storage.objectViewer"

  gcloud storage buckets add-iam-policy-binding gs://{LOGS_BUCKET} \
    --member="serviceAccount:{DEPLOYER_SA_EMAIL}" \
    --role="roles/storage.legacyBucketReader"
  ```

  **4.2. Builder service account**
  - Artifact Registry Writer
  - Artifact Registry Reader (For reading Nonprod Artifact Registry from Prod Builder SA)
  - Logs Writer
  - Storage Object Viewer (Source Staging Bucket)
  - Storage Object Admin/Creator (Bucket level)
  - Storage Legacy Bucket Reader (Bucket Level)
  ```bash
  gcloud projects add-iam-policy-binding {PROJECT_ID} \
    --member="serviceAccount:{BUILDER_SA_EMAIL}" \
    --role="roles/artifactregistry.writer"
  
  ## For Production Deployer SA to read Nonprod Artifact Registry
  gcloud projects add-iam-policy-binding {NONPROD_PROJECT_ID} \
    --member="serviceAccount:{PROD_BUILDER_SA_EMAIL}" \
    --role="roles/artifactregistry.reader"

  gcloud projects add-iam-policy-binding {PROJECT_ID} \
    --member="serviceAccount:{BUILDER_SA_EMAIL}" \
    --role="roles/logging.logWriter"

  ## For Nonprod Builder SA to read Source Staging Bucket
  gcloud storage buckets add-iam-policy-binding gs://{SOURCE_STAGING_BUCKET} \
    --member="serviceAccount:{BUILDER_SA_EMAIL}" \
    --role="roles/storage.objectViewer"

  gcloud storage buckets add-iam-policy-binding gs://{LOGS_BUCKET} \
    --member="serviceAccount:{BUILDER_SA_EMAIL}" \
    --role="roles/storage.objectAdmin"

  gcloud storage buckets add-iam-policy-binding gs://{LOGS_BUCKET} \
    --member="serviceAccount:{BUILDER_SA_EMAIL}" \
    --role="roles/storage.objectCreator"

  gcloud storage buckets add-iam-policy-binding gs://{LOGS_BUCKET} \
    --member="serviceAccount:{BUILDER_SA_EMAIL}" \
    --role="roles/storage.legacyBucketReader"
  ```

  **4.3. Runner service account**
  - Cloud Run Invoker
  - Secret Manager Secret Accessor
  - Storage Object Admin
  - Vertex AI User
  ```bash
  gcloud projects add-iam-policy-binding {PROJECT_ID} \
    --member="serviceAccount:{RUNNER_SA_EMAIL}" \
    --role="roles/run.invoker"

  gcloud projects add-iam-policy-binding {PROJECT_ID} \
    --member="serviceAccount:{RUNNER_SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor"

  gcloud projects add-iam-policy-binding {PROJECT_ID} \
    --member="serviceAccount:{RUNNER_SA_EMAIL}" \
    --role="roles/storage.objectAdmin"

  gcloud projects add-iam-policy-binding {PROJECT_ID} \
    --member="serviceAccount:{RUNNER_SA_EMAIL}" \
    --role="roles/aiplatform.user"
  ```
### 5. Create a Workload Identity Pool
  ```bash
  gcloud iam workload-identity-pools create {POOL_ID} \
    --location="global" \
    --display-name="{POOL_DISPLAY_NAME}"
  ```
  **4.1. Create a Workload Identity Pool Provider with OIDC type**
  - Issuer URI: https://token.actions.githubusercontent.com
  - Attribute Mapping:
    * google.subject: assertion.sub
    * attribute.repository: assertion.repository
    * attribute.actor: assertion.actor
    ```bash
    gcloud iam workload-identity-pools providers create-oidc {PROVIDER_ID} \
      --display-name="{PROVIDER_DISPLAY_NAME}" \
      --workload-identity-pool="{POOL_ID}" \
      --location="global" \
      --issuer-uri="https://token.actions.githubusercontent.com" \
      --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.actor=assertion.actor" \
      --attribute-condition="assertion.repository_owner=='{GITHUB_ORG}' && assertion.repository=='{GITHUB_ORG}/{GITHUB_REPO}'"
    ```
### 6. Get the Workload Identity Pool Provider resource name
  - Format: projects/{PROJECT_NUMBER}/locations/global/workloadIdentityPools/{POOL_ID}/providers/{PROVIDER_ID}
  ```bash
  gcloud iam workload-identity-pools providers describe {PROVIDER_ID} \
    --workload-identity-pool="{POOL_ID}" \
    --location="global" \
    --format="value(name)"
  ```
### 7. Bind the Deployer service account with Workload Identity Pool Provider
  ```bash
  gcloud iam service-accounts add-iam-policy-binding {DEPLOYER_SA_EMAIL} \
    --role="roles/iam.workloadIdentityUser" \
    --member="principalSet://iam.googleapis.com/projects/{PROJECT_NUMBER}/locations/global/workloadIdentityPools/{POOL_ID}/attribute.repository/{GITHUB_ORG}/{GITHUB_REPO}"
  ```

### 8. GitHub Repository Secrets

Add the following secrets to your GitHub Repository (Settings > Secrets and variables > Actions):

| Secret Name | Value Description |
|-------------|-------------------|
| `NONPROD_PROJECT_ID` | GCP Project ID for Nonprod environment |
| `NONPROD_WORKLOAD_IDENTITY_PROVIDER` | WIF Provider resource name for Nonprod |
| `NONPROD_SA_DEPLOYER` | Email of the Deployer SA for Nonprod |
| `NONPROD_SA_RUNNER` | Email of the Runner SA for Nonprod |
| `NONPROD_SA_CLOUD_BUILDER` | Email of the Builder SA for Nonprod |
| `NONPROD_CLOUDBUILD_GCS_LOG_DIR` | GCS directory for Cloud Build logs for Nonprod |
| `NONPROD_CLOUDBUILD_GCS_SOURCE_STG` | GCS directory for Cloud Build source staging for Nonprod |
| `PROD_PROJECT_ID` | GCP Project ID for Prod environment |
| `PROD_WORKLOAD_IDENTITY_PROVIDER` | WIF Provider resource name for Prod |
| `PROD_SA_DEPLOYER` | Email of the Deployer SA for Prod |
| `PROD_SA_RUNNER` | Email of the Runner SA for Prod |
| `PROD_SA_CLOUD_BUILDER` | Email of the Builder SA for Prod |
| `PROD_CLOUDBUILD_GCS_LOG_DIR` | GCS directory for Cloud Build logs for Prod |

### 9. Create secret keys in Secret Manager for both non-prod and prod environment
```bash
echo -n "{YOUR_SECRET_VALUE}" | gcloud secrets create {SECRET_NAME} \
  --data-file=- \
  --replication-policy="automatic"
```

### 10. Create Artifact Registry Repositories
- Nonprod Repository
```bash
gcloud artifacts repositories create nprd-sentiment-mnp-retention-artifact-repo \
  --repository-format=docker \
  --location=asia-southeast1 \
  --description="Nonprod Artifact Registry Repository"
```
- Prod Repository
```bash
gcloud artifacts repositories create prd-sentiment-mnp-retention-artifact-repo \
  --repository-format=docker \
  --location=asia-southeast1 \
  --description="Prod Artifact Registry Repository"
```