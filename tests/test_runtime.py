"""Runtime identity, endpoint safety and OpenAI-compatible client behavior."""

from __future__ import annotations

import importlib
import json
import sys
import types
from types import SimpleNamespace

import pytest

from evalgen.config import find_runtime_api_key
from evalgen.runtime import (
    OPENROUTER_BASE_URL,
    OPENROUTER_RUNTIME,
    RuntimeBackend,
    RuntimeSpec,
    build_runtime_request,
    dependency_provenance,
    execution_provenance,
    local_gpu_runtime,
)


def test_openrouter_preset_is_exact_and_stable():
    assert OPENROUTER_RUNTIME.manifest() == {
        "schema_version": 1,
        "runtime_id": "openrouter",
        "backend": "openrouter",
        "base_url": OPENROUTER_BASE_URL,
        "api_key_env": "OPENROUTER_API_KEY",
        "organization": None,
        "project": None,
        "headers": {
            "HTTP-Referer": "https://github.com/tkhongsap/model-eval-harness",
            "X-Title": "model-eval-harness evalgen",
        },
        "build_metadata": {},
        "allow_insecure_http": False,
    }
    assert OPENROUTER_RUNTIME.fingerprint() == (
        "41e5244ae747f6c5f99e8fdd3975e8a8e7f8a4233f7449ffa64bdcbe7d226d82"
    )


def test_local_gpu_preset_records_reproducible_build_identity():
    runtime = local_gpu_runtime(
        runtime_id="gpu-prod-a100",
        base_url="https://inference.internal.example/v1/",
        api_key_env="GPU_INFERENCE_API_KEY",
        organization="true-ai",
        project="migration",
        headers={"X-Eval-Runtime": "model-eval-harness"},
        build_metadata={
            "image_digest": "sha256:0123456789abcdef",
            "model_revision": "qwen-35b-2026-08-08",
            "server_version": "vllm-0.11.0",
            "gpu_type": "A100-80GB",
        },
    )

    manifest = runtime.manifest()
    assert manifest["backend"] == "openai-compatible"
    assert manifest["base_url"] == "https://inference.internal.example/v1"
    assert manifest["api_key_env"] == "GPU_INFERENCE_API_KEY"
    assert manifest["build_metadata"] == {
        "gpu_type": "A100-80GB",
        "image_digest": "sha256:0123456789abcdef",
        "model_revision": "qwen-35b-2026-08-08",
        "server_version": "vllm-0.11.0",
    }
    assert len(runtime.fingerprint()) == 64
    assert RuntimeSpec.from_manifest(manifest) == runtime
    assert runtime.fingerprint() == RuntimeSpec.from_manifest(manifest).fingerprint()


def test_runtime_manifest_refuses_unknown_fields_and_schema_versions():
    manifest = OPENROUTER_RUNTIME.manifest()
    with pytest.raises(ValueError, match="unknown fields"):
        RuntimeSpec.from_manifest({**manifest, "api_key": "must-never-be-accepted"})
    with pytest.raises(ValueError, match="schema_version"):
        RuntimeSpec.from_manifest({**manifest, "schema_version": 2})


@pytest.mark.parametrize(
    ("base_url", "allow_insecure_http"),
    [
        ("file:///tmp/server", False),
        ("https://user:password@gpu.example/v1", False),
        ("https://gpu.example/v1?token=secret", False),
        ("https://gpu.example/v1#fragment", False),
        ("https://gpu.example/v1/../admin", False),
        ("http://gpu.internal:8000/v1", False),
    ],
)
def test_runtime_refuses_unsafe_endpoints(base_url, allow_insecure_http):
    with pytest.raises(ValueError):
        RuntimeSpec(
            runtime_id="gpu",
            backend=RuntimeBackend.OPENAI_COMPATIBLE,
            base_url=base_url,
            api_key_env="GPU_KEY",
            allow_insecure_http=allow_insecure_http,
        )


def test_runtime_allows_loopback_http_and_explicit_isolated_network_http():
    assert local_gpu_runtime().base_url == "http://127.0.0.1:8000/v1"
    runtime = local_gpu_runtime(
        base_url="http://gpu.internal:8000/v1",
        allow_insecure_http=True,
    )
    assert runtime.base_url == "http://gpu.internal:8000/v1"
    assert runtime.manifest()["allow_insecure_http"] is True


def test_runtime_request_builder_is_pure_and_backend_specific():
    inputs = {
        "model": "candidate/model",
        "messages": [{"role": "user", "content": "ทดสอบ"}],
        "max_tokens": 100,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    openrouter_request = build_runtime_request(OPENROUTER_RUNTIME, **inputs)
    gpu_request = build_runtime_request(local_gpu_runtime(), **inputs)

    assert openrouter_request["extra_body"] == {
        "usage": {"include": True},
        "provider": {"require_parameters": True},
    }
    assert "extra_body" not in gpu_request
    assert {
        key: value for key, value in openrouter_request.items() if key != "extra_body"
    } == gpu_request


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer secret"},
        {"X-API-Key": "secret"},
        {"Cookie": "session=secret"},
        {"X-Arbitrary": "metadata that was not reviewed"},
    ],
)
def test_runtime_refuses_headers_that_could_leak_secrets(headers):
    with pytest.raises(ValueError, match="non-secret metadata allowlist"):
        local_gpu_runtime(headers=headers)


def test_api_key_value_never_enters_runtime_or_provenance():
    secret = "sk-never-write-this-value"
    runtime = local_gpu_runtime(api_key_env="PRIVATE_GPU_KEY")
    value, source = find_runtime_api_key(
        runtime,
        environ={"PRIVATE_GPU_KEY": f"  {secret}  ", "OPENROUTER_API_KEY": "wrong"},
    )
    assert (value, source) == (secret, "PRIVATE_GPU_KEY")

    serialized = json.dumps(
        {
            "repr": repr(runtime),
            "manifest": runtime.manifest(),
            "provenance": execution_provenance(runtime),
            "fingerprint": runtime.fingerprint(),
        },
        sort_keys=True,
    )
    assert secret not in serialized
    assert "PRIVATE_GPU_KEY" in serialized


def test_dependency_provenance_records_exact_or_explicitly_absent(monkeypatch):
    def fake_version(name):
        if name == "openai":
            return "2.17.0"
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr("evalgen.runtime.metadata.version", fake_version)
    assert dependency_provenance(("openai", "missing-package")) == {
        "missing-package": None,
        "openai": "2.17.0",
    }


@pytest.fixture
def client_module(monkeypatch):
    """Import client.py against a tiny SDK stub, including in scorer-only CI."""
    previous = sys.modules.pop("evalgen.client", None)
    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = object
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    module = importlib.import_module("evalgen.client")
    try:
        yield module
    finally:
        sys.modules.pop("evalgen.client", None)
        if previous is not None:
            sys.modules["evalgen.client"] = previous


class _FakeCompletions:
    def __init__(self):
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"product": {}}'),
                    finish_reason="stop",
                )
            ],
            model="served-model-revision",
            id="generation-1",
            provider=None,
            system_fingerprint="server-build-abc",
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=4,
                completion_tokens_details=None,
                cost=None,
            ),
            model_dump=lambda: {"id": "generation-1"},
        )


class _FakeSDK:
    def __init__(self):
        self.completions = _FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


def _sdk_factory(captured):
    def factory(**kwargs):
        sdk = _FakeSDK()
        captured["options"] = kwargs
        captured["sdk"] = sdk
        return sdk

    return factory


def _complete(client, **overrides):
    values = {
        "model": "candidate/model",
        "messages": [{"role": "user", "content": "ทดสอบ"}],
        "max_tokens": 100,
        "temperature": 0.0,
        "top_p": 0.0,
        "seed": 0,
        "response_format": {"type": "json_object"},
    }
    values.update(overrides)
    return client.complete(**values)


def test_legacy_openrouter_client_keeps_default_endpoint_and_request(
    client_module, monkeypatch
):
    captured: dict = {}
    monkeypatch.setattr(client_module, "OpenAI", _sdk_factory(captured))
    client = client_module.OpenRouterClient(" sk-openrouter ", timeout=30.0)
    completion = _complete(client)

    assert captured["options"] == {
        "base_url": OPENROUTER_BASE_URL,
        "api_key": "sk-openrouter",
        "timeout": 30.0,
        "max_retries": 0,
        "default_headers": {
            "HTTP-Referer": "https://github.com/tkhongsap/model-eval-harness",
            "X-Title": "model-eval-harness evalgen",
        },
    }
    request = captured["sdk"].completions.requests[0]
    assert request["extra_body"] == {
        "usage": {"include": True},
        "provider": {"require_parameters": True},
    }
    assert completion.runtime_id == "openrouter"
    assert completion.runtime_backend == "openrouter"
    assert completion.runtime_fingerprint == OPENROUTER_RUNTIME.fingerprint()
    assert completion.system_fingerprint == "server-build-abc"


def test_internal_gpu_client_uses_portable_request_and_runtime_identity(
    client_module, monkeypatch
):
    captured: dict = {}
    monkeypatch.setattr(client_module, "OpenAI", _sdk_factory(captured))
    runtime = local_gpu_runtime(
        runtime_id="gpu-staging",
        base_url="https://gpu.internal.example/v1",
        api_key_env="GPU_KEY",
        organization="true-ai",
        project="eval",
        headers={"X-Eval-Runtime": "harness"},
        build_metadata={"image_digest": "sha256:abc", "model_revision": "r42"},
    )
    client = client_module.OpenAICompatibleClient(
        "local-placeholder", runtime=runtime, timeout=45.0
    )
    completion = _complete(client)

    assert captured["options"] == {
        "base_url": "https://gpu.internal.example/v1",
        "api_key": "local-placeholder",
        "timeout": 45.0,
        "max_retries": 0,
        "default_headers": {"X-Eval-Runtime": "harness"},
        "organization": "true-ai",
        "project": "eval",
    }
    request = captured["sdk"].completions.requests[0]
    assert "extra_body" not in request
    assert request["model"] == "candidate/model"
    assert request["response_format"] == {"type": "json_object"}
    assert completion.runtime_id == "gpu-staging"
    assert completion.runtime_backend == "openai-compatible"
    assert completion.runtime_fingerprint == runtime.fingerprint()


@pytest.mark.parametrize(
    "option",
    [
        {"provider": "DeepInfra"},
        {"reasoning_effort": "none"},
    ],
)
def test_internal_gpu_client_refuses_openrouter_only_options(
    client_module, monkeypatch, option
):
    captured: dict = {}
    monkeypatch.setattr(client_module, "OpenAI", _sdk_factory(captured))
    client = client_module.OpenAICompatibleClient(
        "placeholder", runtime=local_gpu_runtime()
    )

    with pytest.raises(ValueError, match="OpenRouter request extension"):
        _complete(client, **option)
    assert captured["sdk"].completions.requests == []
