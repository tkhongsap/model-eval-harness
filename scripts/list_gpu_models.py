"""List every model available on True's internal GPU endpoints, and whether it actually works.

Two endpoints serve models to this project, and confusing them has already cost time:

  Token Factory   https://token-fac-api.truecorp.co.th/v1  (via 10.94.154.102, pinned cert)
                  key: TOKEN_FACTORY_API_KEY
  Modellismz      https://api.modellismz.app/v1
                  key: EVALGEN_GPU_API_KEY

They are separate stacks with separate catalogs. `gemma-4-12b` has been observed serving
fine on one while failing on the other, so "is Gemma up?" is not a well-formed question
until you say which endpoint.

WHY THIS CHECKS READINESS AND DOES NOT JUST LIST
------------------------------------------------
`Token_Factory_API_Guide.md` states the catalog is dynamic and "clients must check
/v1/models and handle unavailable models" -- it is a list of what your key may address, not
a health check. On 2026-08-15 `/v1/models` listed `gemma-4-12b-it` continuously through a
90-minute outage in which it served nothing, and a runner that trusted the listing burned
11 minutes rediscovering that 150 times. So by default each listed model gets one real
generation call, and only HTTP 200 counts as up. Pass --list-only for the raw catalog.

There is no separate "name" field to report: the OpenAI models API returns `id`,
`object`, `created` and `owned_by`, and `id` IS the name you pass as "model". `created` is
a constant placeholder on both endpoints (identical across every model, and predating the
models themselves), so it is shown only under --raw and never as a deployment date.

The API key values are read from the environment or .env and are never printed.

Run:
    python scripts/list_gpu_models.py                 # catalog + readiness, both endpoints
    python scripts/list_gpu_models.py --list-only     # catalog only, no generation calls
    python scripts/list_gpu_models.py --raw           # also dump the raw JSON per endpoint
    python scripts/list_gpu_models.py --endpoint tf   # tf | modellismz | both (default both)

Exit code: 0 if every listed model answered, 1 if any did not, 2 if an endpoint was
unreachable entirely.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
from dataclasses import dataclass

import httpx

REPO = pathlib.Path(__file__).resolve().parent.parent
CERT = REPO / "configs" / "token-factory.crt.pem"

PROBE = {
    "messages": [{"role": "user", "content": "Reply with exactly: READY"}],
    "max_tokens": 16,
    "temperature": 0,
}

# Truncation widths for the two kinds of detail shown in the table.
REPLY_WIDTH = 40
ERROR_WIDTH = 110
CATALOG_ERROR_WIDTH = 150


@dataclass(frozen=True)
class Endpoint:
    tag: str
    label: str
    base: str            # what we call it, and what is displayed
    key: str
    key_var: str
    verify: str | bool
    connect_ip: str | None = None    # set only where we must connect by address
    host_header: str | None = None

    @property
    def request_base(self) -> str:
        """Where requests are actually sent.

        Only Token Factory differs from `base`: its public DNS name resolves to a host that
        does not serve this API, so we connect by address. The pinned certificate's SAN
        covers both the name and the address, so verification stays ON and is stronger than
        the public-CA default.
        """
        if self.connect_ip is None:
            return self.base
        return f"https://{self.connect_ip}/v1"


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    dotenv = REPO / ".env"
    if dotenv.is_file():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                env.setdefault(name.strip(), value.strip().strip('"').strip("'"))
    return env


def endpoints(env: dict[str, str]) -> list[Endpoint]:
    modellismz_base = env.get("EVALGEN_GPU_BASE_URL") or "https://api.modellismz.app/v1"
    return [
        Endpoint(
            tag="tf",
            label="Token Factory",
            base="https://token-fac-api.truecorp.co.th/v1",
            key=env.get("TOKEN_FACTORY_API_KEY", ""),
            key_var="TOKEN_FACTORY_API_KEY",
            verify=str(CERT),
            connect_ip="10.94.154.102",
            host_header="token-fac-api.truecorp.co.th",
        ),
        Endpoint(
            tag="modellismz",
            label="Modellismz",
            base=modellismz_base.rstrip("/"),
            key=env.get("EVALGEN_GPU_API_KEY", ""),
            key_var="EVALGEN_GPU_API_KEY",
            verify=True,
        ),
    ]


def request(ep: Endpoint, method: str, path: str, timeout: float = 30.0, **kw) -> httpx.Response:
    """One request to an endpoint. `path` is relative to /v1, e.g. "/models"."""
    headers = {"Host": ep.host_header} if ep.host_header else {}
    extensions = {"sni_hostname": ep.host_header} if ep.host_header else {}
    with httpx.Client(verify=ep.verify, timeout=timeout,
                      headers={"Authorization": f"Bearer {ep.key}"}) as client:
        return client.request(method, ep.request_base + path,
                              headers=headers, extensions=extensions, **kw)


def catalog(ep: Endpoint) -> tuple[list[dict] | None, str]:
    """The endpoint's model list, or None and a reason it could not be read."""
    try:
        response = request(ep, "GET", "/models")
    except Exception as exc:                                        # noqa: BLE001
        return None, f"{type(exc).__name__}: {str(exc)[:CATALOG_ERROR_WIDTH]}"
    if response.status_code != 200:
        return None, f"HTTP {response.status_code}: {response.text[:CATALOG_ERROR_WIDTH]}"
    try:
        return response.json().get("data", []), ""
    except ValueError:
        return None, f"unparseable response: {response.text[:CATALOG_ERROR_WIDTH]}"


def reply_text(response: httpx.Response) -> str:
    try:
        return response.json()["choices"][0]["message"]["content"].strip()
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return "(200, unexpected shape)"


def error_detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("error", {}).get("message", response.text)
    except (ValueError, AttributeError):
        detail = response.text
    # The useful half of a LiteLLM backend error is the host:port it could not reach.
    for marker in ("Cannot connect to host ", "APIConnectionError"):
        if marker in detail:
            return detail[detail.index(marker):]
    return detail


def probe(ep: Endpoint, model_id: str) -> tuple[bool, str, float]:
    """One real generation call. Only HTTP 200 counts as up -- see the module docstring."""
    started = time.time()
    try:
        response = request(ep, "POST", "/chat/completions",
                           json={"model": model_id, **PROBE}, timeout=60.0)
    except Exception as exc:                                        # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:ERROR_WIDTH]}", time.time() - started
    elapsed = time.time() - started
    if response.status_code == 200:
        return True, reply_text(response)[:REPLY_WIDTH], elapsed
    return False, f"HTTP {response.status_code}: {error_detail(response)[:ERROR_WIDTH]}", elapsed


def print_header(ep: Endpoint) -> None:
    connect = f"   (connect {ep.connect_ip})" if ep.connect_ip else ""
    auth = f"({len(ep.key)} chars, value never printed)" if ep.key else "NOT SET"
    print("=" * 78)
    print(f"{ep.label}   {ep.base}{connect}")
    print(f"auth  {ep.key_var}  {auth}")
    print("=" * 78)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-only", action="store_true",
                        help="catalog only, no generation calls")
    parser.add_argument("--raw", action="store_true",
                        help="also print the raw /v1/models JSON")
    parser.add_argument("--endpoint", default="both", choices=["tf", "modellismz", "both"])
    args = parser.parse_args()

    selected = [ep for ep in endpoints(load_env())
                if args.endpoint in ("both", ep.tag)]

    unreachable = 0
    probed = 0
    not_ready = 0

    for ep in selected:
        print_header(ep)

        if not ep.key:
            print(f"  skipped: {ep.key_var} is empty\n")
            unreachable += 1
            continue

        models, error = catalog(ep)
        if models is None:
            print(f"  UNREACHABLE  {error}\n")
            unreachable += 1
            continue

        # Sorted copy for display and probing; `models` keeps the order the endpoint returned
        # it in, so --raw stays a faithful dump rather than a reordered one.
        listed = sorted(models, key=lambda m: m.get("id", "?"))
        print(f"  {len(listed)} model(s) in the catalog\n")

        if args.raw:
            print(json.dumps(models, indent=2))
            print()

        if args.list_only:
            for model in listed:
                print(f"  {model.get('id', '?'):26}  owned_by={model.get('owned_by', '?')}")
            print()
            continue

        print(f"  {'MODEL ID':26}  {'STATUS':9}  {'TIME':>7}  DETAIL")
        print(f"  {'-' * 26}  {'-' * 9}  {'-' * 7}  {'-' * 32}")
        for model in listed:
            model_id = model.get("id", "?")
            ready, detail, elapsed = probe(ep, model_id)
            probed += 1
            if not ready:
                not_ready += 1
            print(f"  {model_id:26}  {'READY' if ready else 'FAILED':9}  {elapsed:6.2f}s  {detail}")
        print()

    print("=" * 78)
    if unreachable:
        print(f"{unreachable} endpoint(s) could not be reached at all.")
        print("If that includes Token Factory, check the VPN before reading anything else"
              " into this.")
    if not args.list_only and probed:
        print(f"{probed - not_ready} of {probed} listed models answered a real request.")
        if not_ready:
            print("A model can appear in the catalog and still serve nothing -- the catalog is"
                  " a list\nof what your key may address, not a health check.")
    if unreachable:
        return 2
    return 1 if not_ready else 0


if __name__ == "__main__":
    sys.exit(main())
