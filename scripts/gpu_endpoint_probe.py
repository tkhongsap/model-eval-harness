"""Can this harness reach an internal GPU endpoint, and will it honour the real request?

Answers two different questions, in order, and does not conflate them:

  1. **Is it there?** Ingress, TLS, auth, and the model id the server actually serves.
  2. **Can the harness use it?** Whether the endpoint honours the exact request an arm
     sends -- the pinned decoding, the `response_format` json_schema, the real system
     prompt and a real transcript.

Question 2 is the one that matters and the one a hand-written `curl` will not answer.
Same reasoning as `provider_probe.py`: *"a survey that probed a different request than the
run sends would choose a pin for a call nobody makes."* So the compatibility checks build
their request through `evalgen.prompts.build_messages`, `evalgen.cli.response_format` and
the harness's own `OpenAICompatibleClient` -- not through a curl-shaped approximation that
happens to succeed where the real call would fail.

The credential
--------------
Read through `evalgen.config.find_runtime_api_key`, which resolves exactly the variable
named by the runtime and no alias, because the variable name is part of the runtime's
fingerprint. **The value is never printed, never written, and never returned** -- only its
variable name and whether it was found. Everything this script prints is safe to paste
into a ticket.

Usage
-----
    python scripts/gpu_endpoint_probe.py                       # reads .env
    python scripts/gpu_endpoint_probe.py --base-url ... --model ...
    python scripts/gpu_endpoint_probe.py --quick               # reachability only, 1 call

Exit codes: 0 every check passed; 1 reachable but the harness cannot use it as pinned;
2 not reachable or not authenticated.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from evalgen.cli import response_format  # noqa: E402
from evalgen.config import ENV_FILES, find_runtime_api_key, load_env_file  # noqa: E402
from evalgen.console import configure_stdout  # noqa: E402
from evalgen.prompts import build_base, build_messages  # noqa: E402
from evalgen.runtime import local_gpu_runtime  # noqa: E402
from evalgen.testsets import load_testset  # noqa: E402

DEFAULT_BASE_URL = "https://api.modellismz.app/v1"
DEFAULT_MODEL = "gemma-4-12b"
DEFAULT_ITEM = "RET-01"
PACK = REPO / "tests" / "fixtures" / "testsets" / "retention_v3.jsonl"

# Cloudflare rejects some default user-agents with 403 code 1010. The harness itself is
# unaffected because the OpenAI SDK sets its own, but a raw urllib probe is not, and a
# 403 from that would otherwise read as "the key is wrong".
_UA = "model-eval-harness/gpu_endpoint_probe"

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


class Probe:
    """Collects results so the summary can be a verdict rather than a scroll."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def record(self, status: str, name: str, detail: str = "") -> None:
        self.rows.append((status, name, detail))
        print(f"  [{status}] {name}" + (f"  --  {detail}" if detail else ""))

    def worst(self) -> str:
        if any(status == FAIL for status, _, _ in self.rows):
            return FAIL
        if any(status == WARN for status, _, _ in self.rows):
            return WARN
        return PASS


def _get_json(url: str, *, token: str | None, timeout: float) -> tuple[int, object]:
    """A plain GET. Returns `(status, parsed-or-raw)`; never raises on an HTTP error."""
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        status = exc.code
    except Exception as exc:  # DNS, TLS, refused connection
        return 0, f"{type(exc).__name__}: {exc}"
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body[:400]


def reachability(probe: Probe, base_url: str, token: str | None, timeout: float) -> list[str]:
    """Question 1: is it there, does the key work, and what does it serve?"""
    root = base_url.rstrip("/")
    origin = root[: root.rfind("/v1")] if "/v1" in root else root

    started = time.monotonic()
    status, _ = _get_json(f"{origin}/healthz", token=None, timeout=timeout)
    elapsed = time.monotonic() - started
    if status == 200:
        probe.record(PASS, "ingress /healthz", f"200 in {elapsed:.2f}s")
    elif status == 0:
        probe.record(FAIL, "ingress /healthz", "no response; DNS, TLS or firewall")
        return []
    else:
        probe.record(WARN, "ingress /healthz", f"http {status} (not fatal; may not be exposed)")

    status, payload = _get_json(f"{root}/models", token=None, timeout=timeout)
    if status == 401:
        probe.record(PASS, "auth is enforced", "unauthenticated /v1/models -> 401")
    elif status == 200:
        probe.record(WARN, "auth is enforced", "unauthenticated /v1/models returned 200")
    else:
        probe.record(WARN, "auth is enforced", f"unauthenticated /v1/models -> http {status}")

    if not token:
        probe.record(FAIL, "credential present", "no key found; cannot go further")
        return []

    status, payload = _get_json(f"{root}/models", token=token, timeout=timeout)
    if status != 200 or not isinstance(payload, dict):
        detail = payload if isinstance(payload, str) else json.dumps(payload)[:200]
        probe.record(FAIL, "authenticated /v1/models", f"http {status}: {detail}")
        return []
    served = [
        str(entry.get("id"))
        for entry in payload.get("data", [])
        if isinstance(entry, dict) and entry.get("id")
    ]
    probe.record(PASS, "authenticated /v1/models", f"serves {served}")
    return served


def _client(base_url: str, timeout: float):
    """The harness's own client, against the runtime spec an arm would use."""
    from evalgen.cli import build_client

    runtime = local_gpu_runtime(
        runtime_id="probe", base_url=base_url, api_key_env="EVALGEN_GPU_API_KEY"
    )
    token, name = find_runtime_api_key(runtime)
    if not token:
        raise SystemExit(f"REFUSED: ${name} is not set. The probe never reads a key from a flag.")
    return build_client(token, timeout=timeout, runtime=runtime), runtime


def _one_call(client, *, model, messages, **overrides):
    """One real completion through the harness client. Returns `(ok, detail, completion)`."""
    request = dict(
        model=model,
        messages=messages,
        max_tokens=overrides.pop("max_tokens", 400),
        temperature=overrides.pop("temperature", 0.0),
        top_p=overrides.pop("top_p", 1.0),
        seed=overrides.pop("seed", 0),
    )
    request.update(overrides)
    try:
        completion = client.complete(**request)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:170]}", None
    return True, "", completion


def compatibility(probe: Probe, base_url: str, model: str, item_id: str, timeout: float) -> None:
    """Question 2: will it honour the request an arm actually sends?"""
    client, _runtime = _client(base_url, timeout)
    item = {i.item_id: i for i in load_testset(PACK, app="retention").items}[item_id]
    messages = build_messages(item, build_base())
    schema = response_format()

    # The pinned decoding. Both committed experiment plans pin top_p = 0, so an endpoint
    # that rejects it cannot run them as written -- and that is a finding, not a detail.
    ok, detail, _ = _one_call(client, model=model, messages=messages, top_p=0.0, max_tokens=400)
    if ok:
        probe.record(PASS, "pinned decoding (temp 0, top_p 0, seed 0)", "accepted")
    else:
        probe.record(
            FAIL,
            "pinned decoding (temp 0, top_p 0, seed 0)",
            f"REJECTED -- both committed plans pin top_p=0. {detail}",
        )

    # The real request an arm sends, minus the rejected parameter.
    ok, detail, completion = _one_call(
        client, model=model, messages=messages, response_format=schema, max_tokens=400
    )
    if not ok:
        probe.record(FAIL, "real prompt + json_schema", detail)
        return
    text = completion.content or ""
    try:
        json.loads(text)
        parses = True
    except json.JSONDecodeError:
        parses = False
    probe.record(
        PASS if parses else FAIL,
        "response_format json_schema (strict)",
        f"valid JSON={parses}, {len(text)} chars",
    )

    served = getattr(completion, "observed_model", None)
    if served == model:
        probe.record(PASS, "served model matches the id requested", str(served))
    else:
        probe.record(WARN, "served model matches the id requested", f"requested {model}, served {served}")

    prompt_tokens = getattr(completion, "prompt_tokens", None)
    if prompt_tokens:
        probe.record(PASS, "usage reported", f"prompt_tokens={prompt_tokens}")
    else:
        probe.record(WARN, "usage reported", "no prompt_tokens; token accounting unavailable")

    cost = getattr(completion, "cost", None)
    probe.record(
        PASS if cost else WARN,
        "cost reported",
        str(cost) if cost else "none; cost tables will show a lower bound, not a total",
    )

    # Determinism at the pinned temperature: two identical calls, same answer or not.
    _, _, first = _one_call(client, model=model, messages=messages, response_format=schema)
    _, _, second = _one_call(client, model=model, messages=messages, response_format=schema)
    if first is not None and second is not None:
        same = (first.content or "") == (second.content or "")
        probe.record(
            PASS if same else WARN,
            "two identical calls agree byte for byte",
            "identical" if same else "differ -- expect a nonzero N_flip",
        )


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=os.environ.get("EVALGEN_GPU_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--item", default=DEFAULT_ITEM, help="pack item to send as the real transcript")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--quick", action="store_true", help="reachability only; no model call")
    args = parser.parse_args(argv)

    for path in ENV_FILES:
        load_env_file(path)
    base_url = (args.base_url or DEFAULT_BASE_URL).rstrip("/")
    runtime = local_gpu_runtime(runtime_id="probe", base_url=base_url, api_key_env="EVALGEN_GPU_API_KEY")
    token, key_name = find_runtime_api_key(runtime)

    print(f"endpoint   {base_url}")
    print(f"model      {args.model}")
    print(f"key        ${key_name} {'found' if token else 'NOT SET'}  (value never printed)")
    print()
    print("1. Is it there?")
    probe = Probe()
    served = reachability(probe, base_url, token, args.timeout)
    if served and args.model not in served:
        probe.record(
            FAIL,
            "requested model is served",
            f"{args.model!r} is not in {served}. Some gateways silently fall back to a "
            "default, which produces a full run describing a model nobody chose.",
        )
    elif served:
        probe.record(PASS, "requested model is served", args.model)

    if not args.quick and probe.worst() != FAIL:
        print()
        print("2. Can the harness use it?")
        try:
            compatibility(probe, base_url, args.model, args.item, args.timeout)
        except SystemExit as exc:
            print(exc)
            return 2
        except Exception as exc:  # a probe that crashes must say so, not look clean
            probe.record(FAIL, "compatibility probe", f"{type(exc).__name__}: {str(exc)[:170]}")

    print()
    counts = {status: sum(1 for s, _, _ in probe.rows if s == status) for status in (PASS, WARN, FAIL)}
    print(f"summary  {counts[PASS]} pass, {counts[WARN]} warn, {counts[FAIL]} fail")
    for status, name, detail in probe.rows:
        if status != PASS:
            print(f"  {status}: {name} -- {detail}")
    if counts[FAIL]:
        print("\nThe endpoint cannot serve an arm as currently pinned. Each FAIL above is the reason.")
        return 1
    if counts[WARN]:
        print("\nUsable. Each WARN is a fact to record in the run manifest, not a blocker.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
