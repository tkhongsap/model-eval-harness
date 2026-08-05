"""One real call per OpenRouter provider, so the pin is chosen from measurement.

The Instrument phase pinned `Alibaba` because it is the only non-reasoning endpoint,
which is the regime production runs (`thinkingBudget: 0`). Pinned, it returned 20 of 20
`schema_violation`, every one a bare JSON *number* where the schema's root is an object.
That is a decision the eval cannot make for itself, so this script gathers the facts the
decision needs: for each of the nine endpoints, does it reason, what does its tokenizer
say, does it honour the schema, what does it cost and how long does it take.

Deliberately uses `evalgen.client.OpenRouterClient` and `evalgen.prompts.build_messages`
rather than an ad-hoc request: a survey that probed a different request than the run
sends would choose a pin for a call nobody makes.
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from evalgen.cli import response_format  # noqa: E402
from evalgen.client import OpenRouterClient  # noqa: E402
from evalgen.config import ENV_FILES, find_api_key, load_env_file  # noqa: E402
from evalgen.console import configure_stdout  # noqa: E402
from evalgen.prompts import build_messages, get as get_prompt  # noqa: E402
from evalgen.testsets import load_testset  # noqa: E402

REQUIRED = ("product", "call_event_detection", "recommendation")


def _complete(client, *, extra_reasoning=None, **kwargs):
    """`client.complete`, optionally with OpenRouter's `reasoning` block bolted on.

    The bolt-on goes through the client's own request builder first and is merged into
    the `extra_body` it produced, so the probe measures the real request plus exactly
    one named key rather than a request this script invented.
    """
    if extra_reasoning is None:
        return client.complete(**kwargs)
    from evalgen.client import Completion, _as_dict  # noqa: PLC0415
    from evalgen.request import build_request  # noqa: PLC0415

    body = build_request(**kwargs)
    body["extra_body"]["reasoning"] = extra_reasoning
    started = time.monotonic()
    response = client._client.chat.completions.create(**body)  # noqa: SLF001
    elapsed = time.monotonic() - started
    raw = _as_dict(response)
    usage = raw.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    choice = (raw.get("choices") or [{}])[0]
    return Completion(
        content=(choice.get("message") or {}).get("content"),
        observed_model=raw.get("model"),
        provider=raw.get("provider"),
        finish_reason=choice.get("finish_reason"),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        reasoning_tokens=details.get("reasoning_tokens"),
        cost=usage.get("cost"),
        generation_id=raw.get("id"),
        latency_s=elapsed,
        raw=raw,
    )


def probe(client, model, messages, schema, provider, reasoning=None):
    started = time.monotonic()
    try:
        kwargs = dict(
            model=model,
            messages=messages,
            max_tokens=8000,
            temperature=0.0,
            top_p=0.0,
            seed=0,
            response_format=schema,
            provider=provider,
        )
        if reasoning is not None:
            # Probe only. Sent through the same client so the rest of the body is
            # identical to a real call; `request.build_request` has no `reasoning`
            # parameter and this script must not be the second place a request is built.
            kwargs["extra_reasoning"] = reasoning
        completion = _complete(client, **kwargs)
    except Exception as exc:  # noqa: BLE001 - a survey reports failures, it does not raise
        return {
            "provider_requested": provider,
            "error": f"{type(exc).__name__}: {exc}"[:300],
            "elapsed_s": round(time.monotonic() - started, 1),
        }
    content = completion.content or ""
    verdict = "no_content"
    if content:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            verdict = "not_json"
        else:
            if not isinstance(payload, dict):
                verdict = f"json_but_{type(payload).__name__}"
            elif [k for k in REQUIRED if k not in payload]:
                verdict = "object_missing_keys"
            else:
                verdict = "OK"
    return {
        "provider_requested": provider,
        "provider": completion.provider,
        "observed_model": completion.observed_model,
        "prompt_tokens": completion.prompt_tokens,
        "completion_tokens": completion.completion_tokens,
        "reasoning_tokens": completion.reasoning_tokens,
        "cost": completion.cost,
        "finish_reason": completion.finish_reason,
        "elapsed_s": round(time.monotonic() - started, 1),
        "verdict": verdict,
        "head": content[:90],
    }


def main() -> int:
    configure_stdout()
    model = sys.argv[1]
    item_id = sys.argv[2]
    providers = sys.argv[3].split(",")
    reasoning = json.loads(sys.argv[4]) if len(sys.argv) > 4 else None

    testset = load_testset(REPO / "tests" / "fixtures" / "testsets" / "retention_v1.jsonl")
    item = next(i for i in testset.items if i.item_id == item_id)
    prompt = get_prompt("v9_16_base")
    messages = build_messages(item, prompt)
    schema = response_format()
    for env_file in ENV_FILES:
        load_env_file(env_file)
    key, _ = find_api_key()
    client = OpenRouterClient(key, timeout=300.0)

    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
        rows = list(
            pool.map(
                lambda p: probe(client, model, messages, schema, p, reasoning), providers
            )
        )
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
