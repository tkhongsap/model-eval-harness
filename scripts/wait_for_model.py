"""Poll a Token Factory model until it can actually answer, then exit.

`GET /v1/models` is not the check. On 2026-08-15 `gemma-4-12b-it` stayed listed in the
catalog for the whole time its vLLM backend at `10.94.154.104:8000` was unreachable, and a
150-call arm burned 11 minutes discovering that one 500 at a time. `Token_Factory_API_Guide.md:158`
says as much -- "do not hard-code an assumed catalog into application logic" -- and the
corollary is that a listing is not a readiness probe either.

So this sends one real, tiny generation and treats only a 200 as up.

    python scripts/wait_for_model.py gemma-4-12b-it --timeout 3600

Exit 0 the model answered; 1 the timeout expired; 2 the endpoint or key is broken.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CERT = REPO / "configs" / "token-factory.crt.pem"
MANIFEST = REPO / "configs" / "runtime.token-factory.json"


def _load_env() -> None:
    env = REPO / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def probe(base: str, model: str, key: str, ctx: ssl.SSLContext) -> tuple[bool, str]:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "ok"}],
        "max_tokens": 4,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "X-Eval-Runtime": "model-eval-harness"})
    try:
        with urllib.request.urlopen(req, timeout=90, context=ctx) as r:
            return r.status == 200, f"HTTP {r.status}"
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200].decode("utf-8", "replace")
        return False, f"HTTP {exc.code}: {detail}"
    except Exception as exc:  # noqa: BLE001 - any transport failure means "not ready"
        return False, f"{type(exc).__name__}: {exc}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="wait_for_model")
    ap.add_argument("model")
    ap.add_argument("--timeout", type=float, default=3600, help="seconds (default 1h)")
    ap.add_argument("--interval", type=float, default=60, help="seconds between probes")
    args = ap.parse_args(argv)

    _load_env()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    base = manifest["base_url"].rstrip("/")
    key = os.environ.get(manifest["api_key_env"], "")
    if not key:
        print(f"no {manifest['api_key_env']} in environment or .env")
        return 2
    ctx = ssl.create_default_context(cafile=str(CERT))

    deadline = time.monotonic() + args.timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        up, detail = probe(base, args.model, key, ctx)
        stamp = time.strftime("%H:%M:%S")
        if up:
            print(f"[{stamp}] {args.model} ANSWERED after {attempt} probe(s) -- it is up.")
            return 0
        print(f"[{stamp}] probe {attempt}: {args.model} not ready -- {detail[:150]}", flush=True)
        time.sleep(args.interval)

    print(f"timed out after {args.timeout:.0f}s; {args.model} never answered")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
