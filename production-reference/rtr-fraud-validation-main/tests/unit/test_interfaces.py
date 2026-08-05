"""Unit tests for app/core/interfaces.py — Protocol definitions."""
from __future__ import annotations

import pytest

from app.core.interfaces import AIValidator, Notifier, SecretProvider, StorageReader, StorageWriter
from app.core.models import TokenUsage


# ---------------------------------------------------------------------------
# Concrete stubs that satisfy each Protocol
# ---------------------------------------------------------------------------

class _SecretProviderStub:
    def get(self, key: str) -> str:
        return f"value_for_{key}"


class _StorageReaderStub:
    def read_bytes(self, path: str) -> bytes:
        return b"bytes"


class _StorageWriterStub:
    def write_bytes(self, path: str, data: bytes) -> None:
        pass


class _AIValidatorStub:
    async def validate(self, images_b64: list[str], prompt: str) -> tuple[dict, TokenUsage]:
        return {"result": True}, TokenUsage()


class _NotifierStub:
    async def send(self, recipients: list[str], subject: str, body_html: str, **kwargs: object) -> None:
        pass


# ---------------------------------------------------------------------------
# isinstance checks (cover @runtime_checkable + class bodies)
# ---------------------------------------------------------------------------

def test_secret_provider_isinstance() -> None:
    assert isinstance(_SecretProviderStub(), SecretProvider)


def test_storage_reader_isinstance() -> None:
    assert isinstance(_StorageReaderStub(), StorageReader)


def test_storage_writer_isinstance() -> None:
    assert isinstance(_StorageWriterStub(), StorageWriter)


def test_ai_validator_isinstance() -> None:
    assert isinstance(_AIValidatorStub(), AIValidator)


def test_notifier_isinstance() -> None:
    assert isinstance(_NotifierStub(), Notifier)


def test_non_conforming_object_not_secret_provider() -> None:
    class _Bad:
        pass

    assert not isinstance(_Bad(), SecretProvider)


# ---------------------------------------------------------------------------
# Call each method to cover the `...` statement in each Protocol body
# ---------------------------------------------------------------------------

def test_secret_provider_get() -> None:
    stub = _SecretProviderStub()
    assert stub.get("MY_KEY") == "value_for_MY_KEY"


def test_storage_reader_read_bytes() -> None:
    stub = _StorageReaderStub()
    assert stub.read_bytes("path/to/file") == b"bytes"


def test_storage_writer_write_bytes() -> None:
    stub = _StorageWriterStub()
    stub.write_bytes("path/to/file", b"data")  # should not raise


async def test_ai_validator_validate() -> None:
    stub = _AIValidatorStub()
    result, usage = await stub.validate(["b64img"], "prompt")
    assert result == {"result": True}
    assert isinstance(usage, TokenUsage)


async def test_notifier_send() -> None:
    stub = _NotifierStub()
    await stub.send(["r@x.com"], "Subject", "<p>body</p>")  # should not raise


# ---------------------------------------------------------------------------
# Call Protocol methods directly (covers `...` in Protocol body itself)
# ---------------------------------------------------------------------------

def test_secret_provider_protocol_method_body() -> None:
    """Call the Protocol method body directly — covers the `...` statement."""
    result = SecretProvider.get(None, "key")  # type: ignore[arg-type]
    assert result is None  # `...` returns None


def test_storage_reader_protocol_method_body() -> None:
    result = StorageReader.read_bytes(None, "path")  # type: ignore[arg-type]
    assert result is None


def test_storage_writer_protocol_method_body() -> None:
    result = StorageWriter.write_bytes(None, "path", b"data")  # type: ignore[arg-type]
    assert result is None


async def test_ai_validator_protocol_method_body() -> None:
    result = await AIValidator.validate(None, [], "prompt")  # type: ignore[arg-type]
    assert result is None


async def test_notifier_protocol_method_body() -> None:
    result = await Notifier.send(None, [], "sub", "<p>b</p>")  # type: ignore[arg-type]
    assert result is None
