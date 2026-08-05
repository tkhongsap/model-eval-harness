"""Unit tests for app/processors/shop_processor.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.models import (
    GpsMetadata,
    ProcessStatus,
    ShopRecord,
    ShopResult,
    TokenUsage,
)
from app.processors.shop_processor import ShopProcessor
from tests.conftest import make_jpeg_bytes


def _make_record(
    *,
    original_row_id: int = 1,
    xd_rtr_code: str = "XD001",
    xt_rtr_code: str = "",
    photo_1: str = "bucket/shop/p1.jpg",
    photo_2: str = "bucket/shop/p2.jpg",
    photo_3: str = "bucket/shop/p3.jpg",
) -> ShopRecord:
    return ShopRecord(
        original_row_id=original_row_id,
        xd_rtr_code=xd_rtr_code,
        xt_rtr_code=xt_rtr_code,
        xd_rtr_name="Shop XD",
        xt_rtr_name="",
        photo_1_path=photo_1,
        photo_2_path=photo_2,
        photo_3_path=photo_3,
    )


def _make_processor(
    *,
    s3_bytes: bytes | None = None,
    gemini_response: dict | None = None,
) -> ShopProcessor:
    s3 = MagicMock()
    s3.normalise_key.side_effect = lambda x: x
    s3.read_bytes.return_value = s3_bytes or make_jpeg_bytes()

    gemini = AsyncMock()
    gemini.validate.return_value = (
        gemini_response or {
            "from_other_device": "0/3",
            "shop_operate": "0/3",
            "un_relate": "0",
            "un_relate_category": {},
        },
        TokenUsage(),
    )

    img = MagicMock()
    img.extract_metadata.return_value = GpsMetadata()
    img.compute_same_photo_label.return_value = "0/3"

    return ShopProcessor(s3=s3, gemini=gemini, image_processor=img)


# ---------------------------------------------------------------------------
# Guard: no photos
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_no_photo() -> None:
    record = _make_record(photo_1="", photo_2="", photo_3="")
    proc = _make_processor()
    result = await proc.process(record, "prompt")
    assert result.complaint_status == "inComplaint-No Photo"
    assert result.status == ProcessStatus.FAIL
    assert result.original_row_id == 1


@pytest.mark.asyncio
async def test_process_no_photo_skips_s3_and_gemini() -> None:
    record = _make_record(photo_1="", photo_2="", photo_3="")
    s3 = MagicMock()
    gemini = AsyncMock()
    img = MagicMock()
    proc = ShopProcessor(s3=s3, gemini=gemini, image_processor=img)
    await proc.process(record, "prompt")
    s3.read_bytes.assert_not_called()
    gemini.validate.assert_not_called()


# ---------------------------------------------------------------------------
# Guard: insufficient photos
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_insufficient_photos() -> None:
    # Only 1 valid JPEG path
    record = _make_record(photo_2="", photo_3="")
    proc = _make_processor()
    result = await proc.process(record, "prompt")
    assert result.complaint_status == "incompliant-Less than 3 Photos"
    assert result.status == ProcessStatus.FAIL


# ---------------------------------------------------------------------------
# S3 failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_s3_all_fail_returns_s3_error() -> None:
    record = _make_record()
    s3 = MagicMock()
    s3.normalise_key.side_effect = lambda x: x
    s3.read_bytes.side_effect = Exception("connection refused")
    gemini = AsyncMock()
    img = MagicMock()
    img.extract_metadata.return_value = GpsMetadata()

    proc = ShopProcessor(s3=s3, gemini=gemini, image_processor=img)
    result = await proc.process(record, "prompt")
    assert result.complaint_status == "inComplaint-No Photo"
    assert result.status == ProcessStatus.FAIL
    gemini.validate.assert_not_called()


# ---------------------------------------------------------------------------
# Gemini failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_gemini_failure() -> None:
    record = _make_record()
    s3 = MagicMock()
    s3.normalise_key.side_effect = lambda x: x
    s3.read_bytes.return_value = make_jpeg_bytes()

    gemini = AsyncMock()
    gemini.validate.side_effect = RuntimeError("quota exceeded")

    img = MagicMock()
    img.extract_metadata.return_value = GpsMetadata()
    img.compute_same_photo_label.return_value = "0/3"

    proc = ShopProcessor(s3=s3, gemini=gemini, image_processor=img)
    result = await proc.process(record, "prompt")
    assert result.status == ProcessStatus.FAIL
    assert result.complaint_status == "inComplaint"
    assert "quota exceeded" in result.error_message


# ---------------------------------------------------------------------------
# Happy path — Complaint (no fraud detected)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_success_complaint() -> None:
    record = _make_record()
    proc = _make_processor(
        gemini_response={
            "from_other_device": "0/3",
            "shop_operate": "0/3",
            "un_relate": "0",
            "un_relate_category": {},
        }
    )
    result = await proc.process(record, "prompt")
    assert result.status == ProcessStatus.SUCCESS
    assert result.complaint_status == "Complaint"
    assert result.original_row_id == 1


# ---------------------------------------------------------------------------
# Happy path — inComplaint (fraud detected)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_success_incompliant_from_other_device() -> None:
    record = _make_record()
    proc = _make_processor(
        gemini_response={
            "from_other_device": "2/3",
            "shop_operate": "0/3",
            "un_relate": "0",
            "un_relate_category": {},
        }
    )
    result = await proc.process(record, "prompt")
    assert result.status == ProcessStatus.SUCCESS
    assert result.complaint_status == "inComplaint"


@pytest.mark.asyncio
async def test_process_success_incompliant_closed_business() -> None:
    record = _make_record()
    proc = _make_processor(
        gemini_response={
            "from_other_device": "0/3",
            "shop_operate": "3/3",
            "un_relate": "0",
            "un_relate_category": {},
        }
    )
    result = await proc.process(record, "prompt")
    assert result.complaint_status == "inComplaint"


# ---------------------------------------------------------------------------
# _classify — static, tested directly
# ---------------------------------------------------------------------------

def test_classify_all_zero_returns_complaint() -> None:
    r = ShopResult(same_photo="0/3", from_other_device="0/3", closed_business="0/3")
    assert ShopProcessor._classify(r) == "Complaint"


def test_classify_same_photo_triggers_incompliant() -> None:
    r = ShopResult(same_photo="2/3", from_other_device="0/3", closed_business="0/3")
    assert ShopProcessor._classify(r) == "inComplaint"


def test_classify_from_other_device_triggers_incompliant() -> None:
    r = ShopResult(same_photo="0/3", from_other_device="1/3", closed_business="0/3")
    assert ShopProcessor._classify(r) == "inComplaint"


def test_classify_closed_business_triggers_incompliant() -> None:
    r = ShopResult(same_photo="0/3", from_other_device="0/3", closed_business="3/3")
    assert ShopProcessor._classify(r) == "inComplaint"
