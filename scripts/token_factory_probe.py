"""Does True's internal Token Factory answer, and does each model actually generate?

Two questions, asked in order and not conflated:

  1. **Is it there?** Ingress, TLS, auth, and which model ids the server actually serves.
  2. **Does each model generate?** One real `chat.completions` call per model, with the
     reply printed so a human can see the model said something sensible rather than
     trusting a 200.

This is deliberately the *shallow* probe. It does NOT answer "can the harness use this as
an evaluation arm" -- that needs the pinned decoding, the `response_format` json_schema and
the real prompt, and it is what `scripts/gpu_endpoint_probe.py` exists for. The distinction
is not pedantic: the Modellismz endpoint listed its models cleanly and then **rejected
`top_p = 0`**, which every committed experiment plan pins. A `/v1/models` 200 is worth very
little on its own.

The network path, and the certificate
------------------------------------
`token-fac-api.truecorp.co.th` resolves publicly to an address that does **not** serve this
API -- measured 2026-08-14: `111.84.54.29:443` times out, while `10.94.154.102:443`
connects in ~25 ms. So the address is used directly.

That normally forces a choice between a Host-header override and a certificate mismatch.
Here it forces neither. The endpoint's certificate is **self-signed**, which is why the
public trust store rejects it -- not a name mismatch -- and its SAN covers **both** the
hostname and `10.94.154.102`. So pinning that one certificate
(`configs/token-factory.crt.pem`) and verifying against it works when connecting by
address, with the stock client and no transport tricks.

**Verification is ON, and is stronger than the public-CA default**: exactly one certificate
is trusted, so a substituted host fails even if it holds a certificate some public CA
signed. An earlier version of this script used `verify=False`; that was the wrong fix and
gave up the guarantee that the host is the intended one, on a connection carrying a bearer
token. Rotation breaks the pin deliberately -- a changed certificate should stop the probe
and be re-reviewed.

The credential
--------------
Read from the `TOKEN_FACTORY_API_KEY` environment variable, or from `.env` if it is not
already set. **The value is never printed, never written and never returned** -- only its
variable name, its length, and whether the server accepted it. Everything this script
prints is safe to paste into a ticket.

Usage
-----
    python scripts/token_factory_probe.py                  # list, then generate on each
    python scripts/token_factory_probe.py --model gemma-4-31b-it-fp8
    python scripts/token_factory_probe.py --list-only      # question 1 only, no generation
    python scripts/token_factory_probe.py --prompt "..."   # your own prompt

Exit codes: 0 every model answered; 1 reachable but at least one model did not generate;
2 not reachable, or the key was rejected.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The name is part of the endpoint's identity and is printed; the value never is.
KEY_VAR = "TOKEN_FACTORY_API_KEY"
HOSTNAME = "token-fac-api.truecorp.co.th"
INTERNAL_IP = "10.94.154.102"
CERT_PATH = REPO_ROOT / "configs" / "token-factory.crt.pem"

DEFAULT_PROMPT = (
    "Reply with exactly one short sentence confirming you are running, "
    "and name the model you are."
)

EXIT_OK = 0
EXIT_MODEL_FAILED = 1
EXIT_UNREACHABLE = 2


def _load_key() -> str | None:
    """Environment first, then `.env`. Returns the value or None; never logs it."""
    value = os.environ.get(KEY_VAR, "").strip()
    if value:
        return value
    env_file = REPO_ROOT / ".env"
    if not env_file.is_file():
        return None
    match = re.search(
        rf"^{re.escape(KEY_VAR)}=(.*)$", env_file.read_text(encoding="utf-8"), re.M
    )
    if match is None:
        return None
    return match.group(1).strip() or None


def _client(key: str, timeout: float):
    """An OpenAI client pointed at the endpoint, verifying against the pinned certificate.

    Built on the same `openai` SDK the harness itself calls through, rather than a
    curl-shaped approximation, so a failure here is a failure the harness would also hit.
    Because the certificate's SAN covers the IP, this needs no Host override and no
    disabled verification -- see the module docstring.
    """
    import httpx
    from openai import OpenAI

    if not CERT_PATH.is_file():
        raise SystemExit(
            f"pinned certificate missing at {CERT_PATH}. Re-pin it with "
            "ssl.get_server_certificate(('10.94.154.102', 443)) and review the change."
        )
    return OpenAI(
        api_key=key,
        base_url=f"https://{INTERNAL_IP}/v1",
        http_client=httpx.Client(verify=str(CERT_PATH), timeout=timeout),
        max_retries=0,  # a probe reports the first failure; it does not paper over it
    )



def _reachable() -> tuple[bool, str]:
    import socket

    sock = socket.socket()
    sock.settimeout(6)
    started = time.monotonic()
    try:
        sock.connect((INTERNAL_IP, 443))
        return True, f"{(time.monotonic() - started) * 1000:.0f} ms"
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        sock.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="token_factory_probe", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--model", action="append", default=None,
                        help="repeatable; default is every model the server lists")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--list-only", action="store_true",
                        help="answer question 1 and stop; makes no generation call")
    args = parser.parse_args(argv)

    print(f"endpoint   https://{HOSTNAME}/v1  (via {INTERNAL_IP})")
    print(f"tls        verified against the pinned {CERT_PATH.name}")

    ok, detail = _reachable()
    print(f"tcp        {'reachable, ' + detail if ok else 'UNREACHABLE -- ' + detail}")
    if not ok:
        print("\nNot on the network that serves this address. Nothing else can be checked.")
        return EXIT_UNREACHABLE

    key = _load_key()
    if not key:
        print(f"key        NOT FOUND. Set {KEY_VAR} in the environment or in .env")
        return EXIT_UNREACHABLE
    print(f"key        from {KEY_VAR} ({len(key)} chars; value never printed)")

    client = _client(key, args.timeout)

    # ---- question 1: is it there, and what does it serve? --------------------------
    try:
        listed = [model.id for model in client.models.list().data]
    except Exception as exc:  # noqa: BLE001 - any failure here is one outcome
        print(f"\nMODELS     FAILED -- {type(exc).__name__}: {str(exc)[:300]}")
        return EXIT_UNREACHABLE

    print(f"models     {len(listed)} served: {', '.join(listed)}")

    if args.list_only:
        return EXIT_OK

    targets = args.model or listed
    unknown = [name for name in targets if name not in listed]
    if unknown:
        print(f"\nrefusing: {unknown} not served here. Served: {listed}")
        return EXIT_MODEL_FAILED

    # ---- question 2: does each model actually generate? ----------------------------
    failures: list[str] = []
    for name in targets:
        print(f"\n{'=' * 78}\n{name}\n{'=' * 78}")
        started = time.monotonic()
        try:
            response = client.chat.completions.create(
                model=name,
                messages=[{"role": "user", "content": args.prompt}],
                max_tokens=args.max_tokens,
                temperature=0.0,
            )
        except Exception as exc:  # noqa: BLE001
            failures.append(name)
            print(f"  FAILED after {(time.monotonic() - started):.2f}s")
            print(f"  {type(exc).__name__}: {str(exc)[:400]}")
            continue

        elapsed = time.monotonic() - started
        choice = response.choices[0] if response.choices else None
        content = (getattr(choice.message, "content", None) if choice else None) or ""
        usage = getattr(response, "usage", None)

        print(f"  latency        {elapsed:.2f}s")
        print(f"  observed model {getattr(response, 'model', None)}")
        print(f"  finish_reason  {getattr(choice, 'finish_reason', None)}")
        if usage is not None:
            print(
                f"  tokens         prompt={getattr(usage, 'prompt_tokens', None)} "
                f"completion={getattr(usage, 'completion_tokens', None)}"
            )
        print("  reply:")
        if content.strip():
            for line in content.strip().splitlines():
                print(f"    {line}")
        else:
            # Empty content with a 200 is a real failure mode, not a pass: it is what a
            # model that spent its whole budget on reasoning tokens looks like.
            failures.append(name)
            print("    (EMPTY -- a 200 with no content is not a working model)")

    print(f"\n{'=' * 78}")
    if failures:
        print(f"{len(failures)} of {len(targets)} did not generate: {', '.join(failures)}")
        return EXIT_MODEL_FAILED
    print(f"all {len(targets)} models generated a reply")
    print(
        "\nThis proves the endpoint answers and each model talks. It does NOT prove the\n"
        "harness can use it as an arm -- that needs the pinned decoding and the\n"
        "response_format schema. Run scripts/gpu_endpoint_probe.py for that."
    )
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
