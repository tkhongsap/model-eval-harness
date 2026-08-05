"""Shared pytest fixtures for the RTR fraud-validation test suite."""
from __future__ import annotations

import base64
import io

import numpy as np
import pytest
from PIL import Image


def make_jpeg_bytes(color: tuple[int, int, int] = (128, 64, 32), size: tuple[int, int] = (64, 64)) -> bytes:
    """Return raw JPEG bytes for a solid-colour image."""
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def make_b64_jpeg(color: tuple[int, int, int] = (128, 64, 32), size: tuple[int, int] = (64, 64)) -> str:
    """Return base64-encoded JPEG string."""
    return base64.b64encode(make_jpeg_bytes(color, size)).decode("utf-8")


def make_patterned_jpeg_bytes(pattern: int = 0, size: tuple[int, int] = (64, 64)) -> bytes:
    """Return JPEG bytes for a clearly distinct patterned image.

    Solid-colour images can yield unexpectedly high SSIM because both have
    zero spatial variance.  Checkerboard patterns exercise the structural
    component of SSIM and produce genuinely different images.
    ``pattern`` selects one of several clearly different colour palettes.
    """
    w, h = size
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    palettes: list[tuple[tuple[int, int, int], tuple[int, int, int]]] = [
        ((255, 0, 0), (0, 255, 0)),     # red / green
        ((0, 0, 255), (255, 255, 0)),   # blue / yellow
        ((255, 0, 255), (0, 255, 255)), # magenta / cyan
    ]
    a_col, b_col = palettes[pattern % len(palettes)]
    block = max(w // 4, 1)
    for r in range(h):
        for c in range(w):
            arr[r, c] = a_col if ((r // block + c // block) % 2 == 0) else b_col  # type: ignore[assignment]
    img = Image.fromarray(arr, mode="RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def make_patterned_b64_jpeg(pattern: int = 0, size: tuple[int, int] = (64, 64)) -> str:
    return base64.b64encode(make_patterned_jpeg_bytes(pattern, size)).decode("utf-8")


@pytest.fixture()
def red_b64() -> str:
    return make_b64_jpeg(color=(255, 0, 0))


@pytest.fixture()
def green_b64() -> str:
    return make_b64_jpeg(color=(0, 255, 0))


@pytest.fixture()
def blue_b64() -> str:
    return make_b64_jpeg(color=(0, 0, 255))


@pytest.fixture()
def red_jpeg_bytes() -> bytes:
    return make_jpeg_bytes(color=(255, 0, 0))
