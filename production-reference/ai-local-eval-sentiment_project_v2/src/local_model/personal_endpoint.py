import json
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
with open("output.json", "w", encoding="utf-8") as f:
    json.dump(response.model_dump(), f, indent=2, ensure_ascii=False)