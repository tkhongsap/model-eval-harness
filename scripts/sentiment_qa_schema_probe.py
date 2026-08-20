"""Does enforcing production's REAL response_schema change the output size?

The hypothesis under test: sentiment_qa's 30-40k figure comes from the structured output --
a long extended JSON forced by a big schema. The earlier measurement used
`response_format: {"type": "json_object"}`, which asks for "some JSON" and leans on the
worked example in the prompt. Production instead sends the full 13,457-character
`response_schema` in `generationConfig`, which both constrains decoding AND adds to the
input. This runs both, on the same call, and compares.
"""
import ast
import importlib.util
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

spec = importlib.util.spec_from_file_location("tab", REPO / "scripts" / "token_ab.py")
tab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tab)
import httpx  # noqa: E402

PROD = REPO / "production-reference" / "sentiment-voice-analysis-develop"
src = (PROD / "tasks" / "sentiment_qa" / "prep_payload_task.py").read_text(encoding="utf-8")
fn = [n for n in ast.walk(ast.parse(src))
      if isinstance(n, ast.FunctionDef) and n.name == "_get_analysis_schema"][0]
VERTEX = ast.literal_eval([n for n in ast.walk(fn) if isinstance(n, ast.Return)][0].value)
RS = VERTEX["response_schema"]

TYPES = {"OBJECT": "object", "STRING": "string", "BOOLEAN": "boolean",
         "ARRAY": "array", "NUMBER": "number", "INTEGER": "integer"}


def to_json_schema(node):
    """Vertex responseSchema -> JSON Schema, strict, every key required."""
    out = {}
    t = node.get("type")
    if t:
        out["type"] = TYPES.get(t, t.lower())
    if "description" in node:
        out["description"] = node["description"]
    if "enum" in node:
        out["enum"] = node["enum"]
    if node.get("type") == "OBJECT":
        props = {k: to_json_schema(v) for k, v in (node.get("properties") or {}).items()}
        out["properties"] = props
        out["required"] = list(props)
        out["additionalProperties"] = False
    if node.get("type") == "ARRAY" and "items" in node:
        out["items"] = to_json_schema(node["items"])
    return out


schema = to_json_schema(RS)
print("converted schema:", f"{len(json.dumps(schema)):,}", "chars")

key = os.environ["OPENROUTER_API_KEY"]
system = tab.build_prompts(False)
item, text = tab.transcripts(1)[0]


def call(client, body, response_format):
    payload = {
        "model": "google/gemini-2.5-flash",
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": text}],
        "response_format": response_format,
        "max_tokens": 65535, "seed": 0, "usage": {"include": True},
        **body,
    }
    r = client.post("https://openrouter.ai/api/v1/chat/completions", json=payload,
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"})
    if r.status_code != 200:
        return {"err": f"HTTP {r.status_code}: {r.text[:200]}"}
    d = r.json()
    u = d.get("usage") or {}
    det = u.get("completion_tokens_details") or {}
    content = d["choices"][0]["message"].get("content") or ""
    try:
        parsed = json.loads(content)
        keys = tab.count_keys(parsed)
    except Exception:
        parsed, keys = None, 0
    return {"in": u.get("prompt_tokens"), "out": u.get("completion_tokens"),
            "think": det.get("reasoning_tokens"), "chars": len(content),
            "keys": keys, "finish": d["choices"][0].get("finish_reason"),
            "cost": u.get("cost"), "parsed": parsed}


STRICT = {"type": "json_schema",
          "json_schema": {"name": "qa_analysis", "strict": True, "schema": schema}}
PLAIN = {"type": "json_object"}

with httpx.Client(timeout=900.0) as c:
    for name, rf, body in [
        ("json_object + thinking (what I ran)", PLAIN,
         {"temperature": 1, "top_p": 1, "reasoning": {"effort": "high"}}),
        ("REAL response_schema + thinking", STRICT,
         {"temperature": 1, "top_p": 1, "reasoning": {"effort": "high"}}),
        ("REAL response_schema, thinking off", STRICT,
         {"temperature": 1, "top_p": 1, "reasoning": {"enabled": False}}),
    ]:
        r = call(c, body, rf)
        if "err" in r:
            print(f"{name:38} {r['err']}")
            continue
        print(f"{name:38} in={r['in']:,} out={r['out']:,} think={r['think']:,} "
              f"keys={r['keys']} chars={r['chars']:,} finish={r['finish']}")
        if r["parsed"] is not None and "schema" in name:
            (REPO / "out" / "sentiment-qa-sample-output.json").write_text(
                json.dumps(r["parsed"], ensure_ascii=False, indent=1), encoding="utf-8")
