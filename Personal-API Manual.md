# Modellismz API Manual

OpenAI-compatible access to Gemma 4 12B for text, image, audio, streaming, and
chunked realtime transcription.

## Connection details

| Setting | Value |
| --- | --- |
| API base URL | `https://api.modellismz.app/v1` |
| Model | `gemma-4-12b` |
| Authentication | `Authorization: Bearer <API_KEY>` |
| Realtime URL | `wss://api.modellismz.app/v1/realtime?model=gemma-4-12b` |
| Protocol compatibility | OpenAI Chat Completions API |

An API key is required for every endpoint except `GET /healthz`. Keep the key
on a trusted backend server and load it from an environment variable or secret
manager. Do not embed a long-lived API key in browser JavaScript, mobile
applications, public repositories, screenshots, or support messages.

## Quick start

### cURL

Set the API key without placing it directly in the request command:

```bash
read -s API_KEY
export API_KEY
```

List available models:

```bash
curl --fail-with-body https://api.modellismz.app/v1/models \
  -H "Authorization: Bearer $API_KEY"
```

Create a text completion:

```bash
curl --fail-with-body https://api.modellismz.app/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemma-4-12b",
    "messages": [
      {"role": "user", "content": "Explain model inference in one sentence."}
    ],
    "temperature": 0,
    "max_tokens": 128
  }'
```

### Windows PowerShell

```powershell
$env:API_KEY = Read-Host "API key"

$headers = @{
    Authorization = "Bearer $env:API_KEY"
}

$body = @{
    model = "gemma-4-12b"
    messages = @(
        @{
            role = "user"
            content = "Explain model inference in one sentence."
        }
    )
    temperature = 0
    max_tokens = 128
} | ConvertTo-Json -Depth 10

$response = Invoke-RestMethod `
    -Uri "https://api.modellismz.app/v1/chat/completions" `
    -Method Post `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body

$response.choices[0].message.content
$response.usage
```

### Python with the OpenAI SDK

Install the SDK:

```bash
pip install "openai>=1.109,<2"
```

Set the key:

```bash
export API_KEY="sk-..."
```

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["API_KEY"],
    base_url="https://api.modellismz.app/v1",
    timeout=300.0,
)

response = client.chat.completions.create(
    model="gemma-4-12b",
    messages=[
        {"role": "user", "content": "Explain model inference in one sentence."}
    ],
    temperature=0,
    max_tokens=128,
)

print(response.choices[0].message.content)
print(response.usage)
```

## HTTP endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/healthz` | Public ingress health check |
| `GET` | `/v1/models` | List models available to the API key |
| `POST` | `/v1/chat/completions` | Text or multimodal completion; supports SSE |

Other paths, including LiteLLM administration routes, are not exposed through
the public hostname.

## Authentication

Send the API key as an HTTP Bearer token:

```http
Authorization: Bearer sk-...
```

Example responses:

- `401 Unauthorized`: the key is missing, invalid, expired, or revoked.
- `403 Forbidden`: the key is not permitted to use the requested model.
- `429 Too Many Requests`: an RPM, TPM, budget, or concurrency limit was
  exceeded.

## Text completions

Request:

```json
{
  "model": "gemma-4-12b",
  "messages": [
    {
      "role": "system",
      "content": "Answer clearly and concisely."
    },
    {
      "role": "user",
      "content": "What is retrieval-augmented generation?"
    }
  ],
  "temperature": 0.2,
  "max_tokens": 256
}
```

Response fields follow the OpenAI Chat Completions format:

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "model": "gemma-4-12b",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 31,
    "completion_tokens": 48,
    "total_tokens": 79
  }
}
```

The exact wording, token counts, identifiers, and finish reason will vary.

## Server-Sent Events streaming

Set `stream` to `true`. Set `stream_options.include_usage` to receive final
usage information.

```bash
curl --no-buffer --fail-with-body \
  https://api.modellismz.app/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemma-4-12b",
    "messages": [{"role": "user", "content": "Write a short introduction."}],
    "stream": true,
    "stream_options": {"include_usage": true},
    "max_tokens": 256
  }'
```

The response uses `Content-Type: text/event-stream`. Each event begins with
`data:` and the stream ends with:

```text
data: [DONE]
```

Python:

```python
stream = client.chat.completions.create(
    model="gemma-4-12b",
    messages=[{"role": "user", "content": "Write a short introduction."}],
    stream=True,
    stream_options={"include_usage": True},
    max_tokens=256,
)

for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
    if chunk.usage:
        print(f"\nUsage: {chunk.usage}")
```

## Image input

Supported formats are JPEG, PNG, and WebP. Send the image as a Base64 data URL
inside `image_url`. Remote media URLs are rejected.

Limits:

- One image per request
- Maximum decoded image size: 8 MB
- Maximum image area: 40,000,000 pixels
- Maximum total HTTP request body: 16 MB

```python
import base64
import os
from pathlib import Path
from openai import OpenAI

image_path = Path("image.png")
mime_type = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}[image_path.suffix.lower()]

image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")

client = OpenAI(
    api_key=os.environ["API_KEY"],
    base_url="https://api.modellismz.app/v1",
    timeout=300.0,
)

response = client.chat.completions.create(
    model="gemma-4-12b",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{image_data}"
                    },
                },
                {"type": "text", "text": "Read and summarize this image."},
            ],
        }
    ],
    temperature=0,
    max_tokens=512,
)

print(response.choices[0].message.content)
print(response.usage)
```

## Audio file input

Audio files use `/v1/chat/completions`; this service does not expose
`/v1/audio/transcriptions`.

Supported formats are WAV, MP3, and OGG. Send the file as a Base64 data URL
inside `audio_url`. Remote media URLs are rejected.

Limits:

- One audio item per request
- Maximum duration: 30 seconds
- Maximum decoded audio size: 12 MB
- Maximum total HTTP request body: 16 MB

```python
import base64
import os
from pathlib import Path
from openai import OpenAI

audio_path = Path("speech.wav")
mime_type = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
}[audio_path.suffix.lower()]

audio_data = base64.b64encode(audio_path.read_bytes()).decode("ascii")

client = OpenAI(
    api_key=os.environ["API_KEY"],
    base_url="https://api.modellismz.app/v1",
    timeout=300.0,
)

response = client.chat.completions.create(
    model="gemma-4-12b",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Transcribe the speech in its original language. "
                        "Return only the transcript."
                    ),
                },
                {
                    "type": "audio_url",
                    "audio_url": {
                        "url": f"data:{mime_type};base64,{audio_data}"
                    },
                },
            ],
        }
    ],
    temperature=0,
    max_tokens=512,
)

print(response.choices[0].message.content)
print(response.usage)
```

Gemma 4 is a general multimodal model rather than a dedicated ASR engine.
Transcription accuracy depends on language, noise, speaker clarity, recording
quality, and prompt wording.

## Realtime transcription over WebSocket

Realtime transcription uses a server-to-server WebSocket connection:

```text
wss://api.modellismz.app/v1/realtime?model=gemma-4-12b
```

Authentication is sent during the WebSocket handshake:

```http
Authorization: Bearer sk-...
```

Audio requirements:

| Property | Required value |
| --- | --- |
| Encoding | Signed PCM16 little-endian |
| Sample rate | 16,000 Hz |
| Channels | Mono |
| Data transport | Base64-encoded PCM bytes |
| Recommended append duration | 20–1,000 ms |

Install the Python WebSocket client:

```bash
pip install "websockets>=15,<16"
```

The following example replays a compatible WAV file at realtime speed:

```python
import asyncio
import base64
import json
import os
import wave

import websockets


async def transcribe(path: str) -> None:
    url = "wss://api.modellismz.app/v1/realtime?model=gemma-4-12b"

    with wave.open(path, "rb") as wav:
        audio_format = (
            wav.getnchannels(),
            wav.getsampwidth(),
            wav.getframerate(),
        )
        if audio_format != (1, 2, 16_000):
            raise ValueError("WAV must be mono PCM16 at 16 kHz")

        async with websockets.connect(
            url,
            additional_headers={
                "Authorization": f"Bearer {os.environ['API_KEY']}"
            },
            open_timeout=30,
        ) as websocket:
            while chunk := wav.readframes(3200):  # 200 ms
                await websocket.send(
                    json.dumps(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(chunk).decode("ascii"),
                        }
                    )
                )
                await asyncio.sleep(len(chunk) / 32_000)

            await websocket.send(
                json.dumps(
                    {"type": "input_audio_buffer.commit", "final": True}
                )
            )

            async for raw_event in websocket:
                event = json.loads(raw_event)
                event_type = event.get("type")

                if event_type == "session.created":
                    print("Session:", event["session"]["id"])
                elif event_type == "transcription.segment":
                    print(event["text"], end=" ", flush=True)
                elif event_type == "session.completed":
                    print("\nUsage:", event["usage"])
                    break
                elif event_type == "error":
                    raise RuntimeError(event["error"])


asyncio.run(transcribe("speech-16k-mono.wav"))
```

### Client events

Append PCM audio:

```json
{
  "type": "input_audio_buffer.append",
  "audio": "<base64-pcm16>"
}
```

Flush buffered audio without ending the session:

```json
{
  "type": "input_audio_buffer.commit",
  "final": false
}
```

Flush buffered audio and complete the session:

```json
{
  "type": "input_audio_buffer.commit",
  "final": true
}
```

### Server events

The first event confirms the negotiated session settings:

```json
{
  "type": "session.created",
  "session": {
    "id": "rt_...",
    "model": "gemma-4-12b",
    "audio_format": "pcm16",
    "sample_rate": 16000,
    "channels": 1,
    "window_ms": 4000,
    "overlap_ms": 750
  }
}
```

Each completed window produces a segment:

```json
{
  "type": "transcription.segment",
  "session_id": "rt_...",
  "sequence": 0,
  "start_ms": 0,
  "end_ms": 4000,
  "text": "transcribed text",
  "raw_text": "raw model output",
  "final": true,
  "latency_ms": 850,
  "usage": {
    "prompt_tokens": 120,
    "completion_tokens": 8,
    "total_tokens": 128
  },
  "request_id": "chatcmpl-..."
}
```

The final event contains the combined transcript and cumulative usage:

```json
{
  "type": "session.completed",
  "session_id": "rt_...",
  "text": "combined transcript",
  "usage": {
    "prompt_tokens": 360,
    "completion_tokens": 24,
    "total_tokens": 384
  },
  "duration_ms": 12000
}
```

This interface performs four-second chunked Gemma inference with 750 ms
overlap and overlap-text deduplication. It is not native token-by-token ASR.
Text is emitted after each completed segment, so latency and segmentation may
differ from a dedicated streaming speech recognizer.

WebSocket limits:

- Maximum session duration: 10 minutes
- Idle timeout: 60 seconds
- One active inference per session
- Maximum queued segments: 3

Native browser WebSocket APIs cannot safely attach the required Authorization
header. Use a backend, trusted desktop service, or another server-side client.

## Usage and rate limits

Each successful response includes usage information. Access may be limited by:

| Limit | Meaning |
| --- | --- |
| RPM | Maximum requests per minute |
| TPM | Maximum input plus output tokens per minute |
| Parallel requests | Maximum simultaneous in-flight inference requests |
| Expiration | Time at which the API key stops working |

Image and audio inputs contribute multimodal tokens to TPM. Each realtime
transcription segment invokes a metered completion and consumes quota. A
continuous realtime session creates approximately 15 segments per minute, so
an API key intended for one realtime session should normally have at least
20–30 RPM available.

## Request limits

| Resource | Limit |
| --- | --- |
| HTTP request body | 16 MB |
| Image | 1 item; 8 MB decoded; 40,000,000 pixels |
| Audio file | 1 item; 12 MB decoded; 30 seconds |
| Media source | Base64 data URLs only; remote URLs are rejected |
| Context window | 8,192 tokens, including multimodal input and output |
| Requested output | Up to 4,096 tokens |
| WebSocket append | 20–1,000 ms of PCM16 audio |
| WebSocket session | 10 minutes |
| WebSocket idle timeout | 60 seconds |

Base64 encoding increases payload size by approximately 33%. Keep the encoded
JSON request below the 16 MB HTTP body limit.

## Errors and retry behavior

Errors use an OpenAI-compatible JSON envelope when possible:

```json
{
  "error": {
    "message": "...",
    "type": "...",
    "code": "..."
  }
}
```

| Status | Meaning | Recommended action |
| --- | --- | --- |
| `400` | Invalid request, malformed Base64, or remote media URL | Correct the request; do not retry unchanged |
| `401` | Missing, invalid, expired, or revoked key | Verify or replace the API key |
| `403` | Model or route is not allowed for the key | Request the required permission |
| `404` | Route is not publicly available | Verify the endpoint path |
| `413` | Body, media size, pixel count, or audio duration limit exceeded | Reduce the input |
| `415` | Unsupported media type or MIME/payload mismatch | Convert to a supported format |
| `422` | Request shape or upstream validation failed | Correct the request fields |
| `429` | RPM, TPM, budget, or concurrency limit exceeded | Wait and retry with backoff |
| `5xx` | Temporary gateway, model, or infrastructure failure | Retry a limited number of times |

For `429` and transient `5xx` responses, honor `Retry-After` when present.
Otherwise use exponential backoff with jitter, for example 1, 2, 4, and 8
seconds, with a maximum delay of 30 seconds. Do not retry `400`, `401`, `403`,
`404`, `413`, `415`, or `422` without changing the request or credentials.

## Data handling

The service is configured not to retain prompt text, images, audio, generated
output, or transcripts in usage logs. Operational metadata such as API-key
hash, model, token usage, latency, request status, and timestamps may be
retained for metering, quota enforcement, troubleshooting, and reporting.

## Integration checklist

Before deployment, verify that:

1. `GET /v1/models` succeeds with the assigned API key.
2. The application uses `gemma-4-12b` exactly as the model name.
3. The API key is stored only in a backend secret store or environment
   variable.
4. Client timeouts allow up to 300 seconds for multimodal requests.
5. Streaming clients read until `data: [DONE]`.
6. Realtime clients send PCM16, 16 kHz, mono audio and handle every documented
   event type.
7. The application handles `401`, `403`, and `429` without exposing the API key
   in logs or user-facing errors.
