### To run code local
1. login to gcp <br>
```gcloud auth login``` <br>
or <br>
```gcloud auth application-default login```
2. set project <br>
```gcloud config set project <PROJECT_ID>```
3. set project quota <br>
```gcloud auth application-default set-quota-project <PROJECT_ID>```
4. create virtual environment <br>
```python -m venv .venv```
5. activate virtual environment <br>
```.venv\Scripts\Activate.ps1```
6. install requirements <br>
```pip install -r requirements.txt```
7. change ```.env.example``` to ```.env``` and fill variables with your own value
8. execute code <br>
```python -m src.main```

### To Check batch status (for development)
1. fill job name in check.py
```job_name = "projects/3888295648/locations/global/batchPredictionJobs/xxxxxxxxxxxxxxx"```
2. execute code <br>
```python -m src.check```

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

### To automate deploy to GCP
1. **Connect GCP Cloud Build to GitHub Repository**
   *Use separate Cloud Build triggers to isolate environment permissions:*

   * **Non-Prod Project** (Development)
     - **Branch:** `dev`
     - **Trigger:** Pull Request
   
   * **Prod Project** (Production)
     - **Branch:** `main`
     - **Trigger:** Push to branch
     - ⚠️ **Note:** Requires manual approval before execution.

2. **Verification**
   - Test the pipeline by creating a Pull Request to the `dev` branch.
   

## Example input.json
```
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
```
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
