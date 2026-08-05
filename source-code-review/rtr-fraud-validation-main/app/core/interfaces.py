"""Protocol interfaces for RTR fraud validation services.

Using ``typing.Protocol`` (structural subtyping) means concrete classes satisfy
an interface implicitly — no inheritance required.  This lets tests inject simple
mock objects without subclassing.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.models import TokenUsage


@runtime_checkable
class SecretProvider(Protocol):
    """Read a secret by key."""

    def get(self, key: str) -> str: ...


@runtime_checkable
class StorageReader(Protocol):
    """Read raw bytes from an addressable path."""

    def read_bytes(self, path: str) -> bytes: ...


@runtime_checkable
class StorageWriter(Protocol):
    """Write raw bytes to an addressable path."""

    def write_bytes(self, path: str, data: bytes) -> None: ...


@runtime_checkable
class AIValidator(Protocol):
    """Send images to an AI model and get back a fraud-detection result dict."""

    async def validate(
        self, images_b64: list[str], prompt: str
    ) -> tuple[dict, TokenUsage]: ...


@runtime_checkable
class Notifier(Protocol):
    """Send a notification (email, webhook, etc.)."""

    async def send(
        self,
        recipients: list[str],
        subject: str,
        body_html: str,
        **kwargs: object,
    ) -> None: ...
