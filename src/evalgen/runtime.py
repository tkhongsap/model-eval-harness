"""Reproducible, non-secret model-runtime configuration.

An evaluation arm is not only a model id.  The gateway, serving stack, image and
endpoint policy can change the system being measured even when ``model`` does not.
This module gives that runtime a typed identity without ever carrying credentials.

``RuntimeSpec`` is deliberately importable without the OpenAI SDK.  Dry runs,
manifest inspection and scoring environments can therefore validate and fingerprint
a runtime without installing a model client or gaining network capability.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
from dataclasses import dataclass
from enum import Enum
from importlib import metadata
from ipaddress import ip_address
from typing import Any, Final, Mapping, Sequence, cast
from urllib.parse import unquote, urlsplit, urlunsplit

from evalgen.request import build_request

__all__ = [
    "OPENROUTER_BASE_URL",
    "OPENROUTER_RUNTIME",
    "RuntimeBackend",
    "RuntimeSpec",
    "build_runtime_request",
    "dependency_provenance",
    "execution_provenance",
    "local_gpu_runtime",
]


OPENROUTER_BASE_URL: Final = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_HEADERS: Final = (
    ("HTTP-Referer", "https://github.com/tkhongsap/model-eval-harness"),
    ("X-Title", "model-eval-harness evalgen"),
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_METADATA_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_HEADER_NAMES = frozenset(
    {
        "http-referer",
        "user-agent",
        "x-client-name",
        "x-eval-runtime",
        "x-title",
    }
)
_SENSITIVE_NAME_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)


class RuntimeBackend(str, Enum):
    """Request dialect used by an OpenAI-compatible endpoint."""

    OPENROUTER = "openrouter"
    OPENAI_COMPATIBLE = "openai-compatible"


def _text(value: str, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    if any(character in normalized for character in ("\r", "\n", "\x00")):
        raise ValueError(f"{field} must not contain control characters")
    return normalized


def _is_loopback(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _canonical_base_url(value: str, *, allow_insecure_http: bool) -> str:
    value = _text(value, field="base_url")
    if any(character.isspace() for character in value):
        raise ValueError("base_url must not contain whitespace")

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(
            "base_url must use https, or http for an explicitly allowed endpoint"
        )
    if not parsed.hostname:
        raise ValueError("base_url must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain a query string or fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"base_url has an invalid port: {exc}") from exc

    decoded_segments = unquote(parsed.path).split("/")
    if any(segment in {".", ".."} for segment in decoded_segments):
        raise ValueError("base_url must not contain relative path segments")

    if (
        parsed.scheme == "http"
        and not _is_loopback(parsed.hostname)
        and not allow_insecure_http
    ):
        raise ValueError(
            "plain HTTP is allowed automatically only for loopback endpoints; "
            "set allow_insecure_http=True explicitly for an isolated trusted network"
        )

    # Normalize only syntax that does not change the endpoint.  In particular, keep
    # the path and its case: OpenAI-compatible gateways are free to route on both.
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


def _pairs(
    values: Mapping[str, str] | Sequence[tuple[str, str]],
    *,
    field: str,
    safe_header_names: bool = False,
) -> tuple[tuple[str, str], ...]:
    if isinstance(values, Mapping):
        items = values.items()
    elif isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        items = values
    else:
        raise TypeError(f"{field} must be a mapping or a sequence of key/value pairs")
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_key, raw_value in items:
        key = _text(raw_key, field=f"{field} key")
        value = _text(raw_value, field=f"{field}[{key!r}]")
        folded = key.casefold()
        if folded in seen:
            raise ValueError(f"{field} contains duplicate key {key!r}")
        seen.add(folded)

        if safe_header_names:
            if folded not in _SAFE_HEADER_NAMES:
                raise ValueError(
                    f"header {key!r} is not in the non-secret metadata allowlist; "
                    "authentication belongs in api_key_env"
                )
        else:
            if not _METADATA_KEY.fullmatch(key):
                raise ValueError(f"{field} key {key!r} is not a portable identifier")
            if any(part in folded for part in _SENSITIVE_NAME_PARTS):
                raise ValueError(
                    f"{field} key {key!r} looks sensitive; build metadata must be "
                    "safe to serialize"
                )
        normalized.append((key, value))
    return tuple(sorted(normalized, key=lambda pair: pair[0].casefold()))


@dataclass(frozen=True)
class RuntimeSpec:
    """The non-secret identity and connection policy for one serving runtime.

    ``api_key_env`` is the *name* of an environment variable.  The value is resolved
    only when a caller elects to construct a network client; it is never a field on
    this dataclass and consequently cannot enter ``repr``, a manifest or a hash.

    ``headers`` accepts only an explicit metadata allowlist.  Authorization, cookies
    and arbitrary vendor headers are intentionally refused because this complete
    object is safe to serialize.  ``organization`` and ``project`` are identifiers,
    not credentials; deployments that treat them as secrets should leave them unset.

    ``build_metadata`` should hold immutable deployment facts where available, such
    as ``image_digest``, ``model_revision``, ``server_version`` and ``gpu_type``.
    Unknown facts stay absent rather than becoming guessed values.
    """

    runtime_id: str
    backend: RuntimeBackend
    base_url: str
    api_key_env: str
    organization: str | None = None
    project: str | None = None
    headers: tuple[tuple[str, str], ...] = ()
    build_metadata: tuple[tuple[str, str], ...] = ()
    allow_insecure_http: bool = False

    def __post_init__(self) -> None:
        runtime_id = _text(self.runtime_id, field="runtime_id")
        if not _IDENTIFIER.fullmatch(runtime_id):
            raise ValueError(
                "runtime_id must be 1-128 characters containing only letters, "
                "numbers, dot, underscore or hyphen"
            )
        object.__setattr__(self, "runtime_id", runtime_id)

        try:
            backend = RuntimeBackend(self.backend)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"unknown backend {self.backend!r}; expected one of "
                f"{[value.value for value in RuntimeBackend]}"
            ) from exc
        object.__setattr__(self, "backend", backend)

        if not isinstance(self.allow_insecure_http, bool):
            raise TypeError("allow_insecure_http must be bool")
        base_url = _canonical_base_url(
            self.base_url, allow_insecure_http=self.allow_insecure_http
        )
        object.__setattr__(self, "base_url", base_url)

        api_key_env = _text(self.api_key_env, field="api_key_env")
        if not _ENV_NAME.fullmatch(api_key_env):
            raise ValueError("api_key_env must be a valid environment-variable name")
        object.__setattr__(self, "api_key_env", api_key_env)

        for field in ("organization", "project"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _text(value, field=field))

        object.__setattr__(
            self,
            "headers",
            _pairs(self.headers, field="headers", safe_header_names=True),
        )
        object.__setattr__(
            self,
            "build_metadata",
            _pairs(self.build_metadata, field="build_metadata"),
        )

        if self.backend is RuntimeBackend.OPENROUTER:
            parsed = urlsplit(self.base_url)
            if parsed.scheme != "https":
                raise ValueError("OpenRouter runtimes must use HTTPS")

    @classmethod
    def openrouter(
        cls,
        *,
        runtime_id: str = "openrouter",
        api_key_env: str = "OPENROUTER_API_KEY",
        base_url: str = OPENROUTER_BASE_URL,
        build_metadata: Mapping[str, str] | Sequence[tuple[str, str]] = (),
    ) -> RuntimeSpec:
        """Return the historical OpenRouter configuration."""
        return cls(
            runtime_id=runtime_id,
            backend=RuntimeBackend.OPENROUTER,
            base_url=base_url,
            api_key_env=api_key_env,
            headers=OPENROUTER_DEFAULT_HEADERS,
            build_metadata=build_metadata,  # type: ignore[arg-type]
        )

    @classmethod
    def from_manifest(cls, manifest: Mapping[str, object]) -> RuntimeSpec:
        """Validate and reconstruct an exact schema-v1 runtime manifest.

        Unknown fields are refused rather than ignored.  A misspelled security flag
        must not yield a valid-looking runtime with different connection behavior.
        """
        required = {"runtime_id", "backend", "base_url", "api_key_env"}
        allowed = required | {
            "schema_version",
            "organization",
            "project",
            "headers",
            "build_metadata",
            "allow_insecure_http",
        }
        unknown = set(manifest) - allowed
        missing = required - set(manifest)
        if unknown:
            raise ValueError(f"runtime manifest has unknown fields: {sorted(unknown)}")
        if missing:
            raise ValueError(f"runtime manifest is missing fields: {sorted(missing)}")
        if manifest.get("schema_version", 1) != 1:
            raise ValueError(
                f"unsupported runtime schema_version {manifest.get('schema_version')!r}"
            )

        return cls(
            runtime_id=cast(str, manifest["runtime_id"]),
            backend=cast(RuntimeBackend, manifest["backend"]),
            base_url=cast(str, manifest["base_url"]),
            api_key_env=cast(str, manifest["api_key_env"]),
            organization=cast(str | None, manifest.get("organization")),
            project=cast(str | None, manifest.get("project")),
            headers=cast(
                Mapping[str, str] | Sequence[tuple[str, str]],
                manifest.get("headers", ()),
            ),  # type: ignore[arg-type]
            build_metadata=cast(
                Mapping[str, str] | Sequence[tuple[str, str]],
                manifest.get("build_metadata", ()),
            ),  # type: ignore[arg-type]
            allow_insecure_http=cast(
                bool, manifest.get("allow_insecure_http", False)
            ),
        )

    def manifest(self) -> dict[str, object]:
        """Return the exact canonical non-secret manifest representation."""
        return {
            "schema_version": 1,
            "runtime_id": self.runtime_id,
            "backend": self.backend.value,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "organization": self.organization,
            "project": self.project,
            "headers": {key: value for key, value in self.headers},
            "build_metadata": {key: value for key, value in self.build_metadata},
            "allow_insecure_http": self.allow_insecure_http,
        }

    def fingerprint(self) -> str:
        """SHA-256 of the canonical non-secret manifest bytes."""
        encoded = json.dumps(
            self.manifest(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


OPENROUTER_RUNTIME: Final = RuntimeSpec.openrouter()


def local_gpu_runtime(
    *,
    runtime_id: str = "local-gpu",
    base_url: str = "http://127.0.0.1:8000/v1",
    api_key_env: str = "EVALGEN_GPU_API_KEY",
    organization: str | None = None,
    project: str | None = None,
    headers: Mapping[str, str] | Sequence[tuple[str, str]] = (),
    build_metadata: Mapping[str, str] | Sequence[tuple[str, str]] = (),
    allow_insecure_http: bool = False,
) -> RuntimeSpec:
    """Build a preset for vLLM/TGI/SGLang or another OpenAI-compatible server.

    The default is a loopback v1 endpoint and therefore needs no insecure-network
    opt-in.  A plain-HTTP hostname on a private cluster must be acknowledged with
    ``allow_insecure_http=True``; an HTTPS cluster requires no exception.
    """
    return RuntimeSpec(
        runtime_id=runtime_id,
        backend=RuntimeBackend.OPENAI_COMPATIBLE,
        base_url=base_url,
        api_key_env=api_key_env,
        organization=organization,
        project=project,
        headers=headers,  # type: ignore[arg-type]
        build_metadata=build_metadata,  # type: ignore[arg-type]
        allow_insecure_http=allow_insecure_http,
    )


def build_runtime_request(
    runtime: RuntimeSpec,
    *,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    max_tokens: int,
    temperature: float,
    top_p: float | None = None,
    seed: int | None = None,
    tools: Sequence[Mapping[str, Any]] | None = None,
    tool_choice: Any | None = None,
    response_format: Mapping[str, Any] | None = None,
    provider: str | None = None,
    reasoning_effort: str = "provider-default",
) -> dict[str, Any]:
    """Build the exact request body for a runtime without importing the SDK.

    This is the seam both a dry run and the real client use.  OpenRouter retains its
    historical usage/routing extensions.  A generic server receives only portable
    chat-completions fields, and OpenRouter-only options are refused instead of
    disappearing from the reviewed workload.
    """
    if runtime.backend is RuntimeBackend.OPENAI_COMPATIBLE:
        if provider is not None:
            raise ValueError(
                "provider pinning is an OpenRouter request extension and cannot "
                f"be used with runtime {runtime.runtime_id!r}"
            )
        if reasoning_effort != "provider-default":
            raise ValueError(
                "normalized reasoning_effort is an OpenRouter request extension "
                f"and cannot be used with runtime {runtime.runtime_id!r}; encode a "
                "serving-stack-specific model variant in the runtime and model "
                "identities instead"
            )

    request = build_request(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        seed=seed,
        tools=tools,
        tool_choice=tool_choice,
        response_format=response_format,
        provider=provider,
        reasoning_effort=reasoning_effort,
    )
    if runtime.backend is RuntimeBackend.OPENAI_COMPATIBLE:
        # ``extra_body`` contains only the OpenRouter usage, routing and normalized
        # reasoning extensions.  Standard OpenAI-compatible fields remain unchanged.
        request.pop("extra_body", None)
    return request


def dependency_provenance(
    distributions: Sequence[str] = ("httpx", "openai"),
) -> dict[str, str | None]:
    """Return installed distribution versions without importing those packages.

    ``None`` is explicit: it proves the dependency was requested for the manifest but
    absent in this environment.  Names are normalized and sorted, making the result
    byte-stable under canonical JSON serialization.
    """
    result: dict[str, str | None] = {}
    for raw_name in sorted(set(distributions), key=str.casefold):
        name = _text(raw_name, field="distribution name")
        try:
            result[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            result[name] = None
    return result


def execution_provenance(runtime: RuntimeSpec) -> dict[str, object]:
    """Return reproducible client/runtime facts suitable for a run manifest."""
    return {
        "schema_version": 1,
        "runtime": runtime.manifest(),
        "runtime_fingerprint": runtime.fingerprint(),
        "dependencies": dependency_provenance(),
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
    }
