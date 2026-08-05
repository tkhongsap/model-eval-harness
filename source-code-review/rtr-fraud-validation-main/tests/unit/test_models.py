"""Unit tests for app/core/models.py."""
from __future__ import annotations

from app.core.models import (
    DetectionResult,
    GpsMetadata,
    ProcessStatus,
    ShopRecord,
    ShopResult,
    TokenUsage,
)

# ---------------------------------------------------------------------------
# ShopResult.to_output_list
# ---------------------------------------------------------------------------

_APPENDED_SCHEMA_LEN = 22


def test_shop_result_to_output_list_length() -> None:
    result = ShopResult()
    assert len(result.to_output_list()) == _APPENDED_SCHEMA_LEN


def test_shop_result_to_output_list_order() -> None:
    result = ShopResult(
        run_date="2024-01-15",
        folder_name="folder",
        rtr_code="RTR001",
        rtr_name="Shop One",
        number_of_images=3,
        photo_name_1="p1.jpg",
        photo_name_2="p2.jpg",
        photo_name_3="p3.jpg",
        photo1_lat="13.7",
        photo1_long="100.5",
        rtr1_lat="13.71",
        rtr1_long="100.51",
        photo1_flag300="Match",
        same_photo="0/3",
        from_other_device="0/3",
        closed_business="0/3",
        un_relate="0",
        un_relate_human="0",
        un_relate_animal="0",
        un_relate_location="0",
        un_relate_object="0",
        complaint_status="Complaint",
    )
    out = result.to_output_list()
    assert out[0] == "2024-01-15"
    assert out[2] == "RTR001"
    assert out[4] == 3
    assert out[-1] == "Complaint"


# ---------------------------------------------------------------------------
# ShopResult factory methods
# ---------------------------------------------------------------------------

def _make_record(**kwargs: str) -> ShopRecord:
    defaults: dict = {
        "original_row_id": 7,
        "xd_rtr_code": "XD001",
        "xt_rtr_code": "XT001",
        "xd_rtr_name": "Shop XD",
        "xt_rtr_name": "Shop XT",
        "photo_1_path": "bucket/shop/p1.jpg",
        "photo_2_path": "bucket/shop/p2.jpg",
        "photo_3_path": "bucket/shop/p3.jpg",
    }
    defaults.update(kwargs)  # type: ignore[arg-type]
    return ShopRecord(**defaults)  # type: ignore[arg-type]


def test_no_photo_factory() -> None:
    record = _make_record(photo_1_path="", photo_2_path="", photo_3_path="")
    r = ShopResult.no_photo(record, 0, 0, 0, 0, 'match')
    assert r.complaint_status == "inComplaint-No Photo"
    assert r.status == ProcessStatus.FAIL
    assert r.original_row_id == 7
    assert r.rtr_code == "XD001"


def test_insufficient_photos_factory() -> None:
    record = _make_record(photo_2_path="", photo_3_path="")
    r = ShopResult.insufficient_photos(record, 0, 0, 0, 0, 'match')
    assert r.complaint_status == "incompliant-Less than 3 Photos"
    assert r.status == ProcessStatus.FAIL


def test_s3_error_factory() -> None:
    record = _make_record()
    r = ShopResult.s3_error(record, "connection timeout")
    assert r.complaint_status == "inComplaint-No Photo"
    assert r.status == ProcessStatus.FAIL
    assert "connection timeout" in r.error_message


def test_unhandled_error_factory() -> None:
    record = _make_record()
    r = ShopResult.unhandled_error(record, "boom")
    assert r.status == ProcessStatus.ERROR
    assert "boom" in r.error_message


def test_factory_original_row_id_propagated() -> None:
    record = _make_record(original_row_id=42)  # type: ignore[arg-type]
    for factory in [ShopResult.no_photo, ShopResult.insufficient_photos]:
        r = factory(record, 0, 0, 0, 0, 'match')  # type: ignore[call-arg]
        assert r.original_row_id == 42
    
    for factory in [ShopResult.s3_error, ShopResult.unhandled_error]:
        r = factory(record)  # type: ignore[call-arg]
        assert r.original_row_id == 42


# ---------------------------------------------------------------------------
# ShopRecord helpers
# ---------------------------------------------------------------------------

def test_shop_record_rtr_code_xd_priority() -> None:
    r = ShopRecord(original_row_id=1, xd_rtr_code="XD", xt_rtr_code="XT")
    assert r.rtr_code == "XD"


def test_shop_record_rtr_code_xt_fallback() -> None:
    r = ShopRecord(original_row_id=1, xd_rtr_code="", xt_rtr_code="XT")
    assert r.rtr_code == "XT"


def test_shop_record_image_paths_filters_invalid() -> None:
    r = ShopRecord(
        original_row_id=1,
        photo_1_path="a.jpg",
        photo_2_path="",          # empty — excluded
        photo_3_path="b.txt",     # wrong extension — excluded
    )
    assert r.image_paths == ["a.jpg"]


def test_shop_record_from_row() -> None:
    row = {
        "original_row_id": 5,
        "xd_rtr_code": "X1",
        "xt_rtr_code": "",
        "xd_rtr_name": "Shop",
        "xt_rtr_name": None,
        "photo_1_path": "p1.jpg",
        "photo_2_path": None,
        "photo_3_path": "p3.png",
    }
    record = ShopRecord.from_row(row)
    assert record.original_row_id == 5
    assert record.xd_rtr_code == "X1"
    assert record.xt_rtr_name == ""
    assert record.photo_2_path == ""


# ---------------------------------------------------------------------------
# TokenUsage
# ---------------------------------------------------------------------------

def test_token_usage_total_input() -> None:
    t = TokenUsage(text_input_tokens=100, image_input_tokens=200)
    assert t.total_input_tokens == 300


def test_token_usage_to_dict_keys() -> None:
    t = TokenUsage()
    d = t.to_dict()
    assert set(d.keys()) == {
        "text_input_tokens",
        "image_input_tokens",
        "text_cache_tokens",
        "image_cache_tokens",
        "output_tokens",
    }


# ---------------------------------------------------------------------------
# DetectionResult.from_dict
# ---------------------------------------------------------------------------

def test_detection_result_from_dict_defaults() -> None:
    d = DetectionResult.from_dict({})
    assert d.from_other_device == "0/0"
    assert d.shop_operate == "0/0"
    assert d.un_relate == "0"


def test_detection_result_from_dict_full() -> None:
    raw = {
        "from_other_device": "2/3",
        "shop_operate": "1/3",
        "un_relate": "1",
        "un_relate_category": {
            "un_relate_human": "1",
            "un_relate_animal": "0",
            "un_relate_location": "1",
            "un_relate_object": "0",
        },
    }
    usage = TokenUsage(text_input_tokens=50, output_tokens=10)
    d = DetectionResult.from_dict(raw, usage)
    assert d.from_other_device == "2/3"
    assert d.un_relate_category.un_relate_human == "1"
    assert d.token_usage.text_input_tokens == 50


# ---------------------------------------------------------------------------
# GpsMetadata
# ---------------------------------------------------------------------------

def test_gps_metadata_to_list_length() -> None:
    g = GpsMetadata(photo_lat=1.0, photo_lon=2.0, rtr_lat=3.0, rtr_lon=4.0, flag="Match")
    lst = g.to_list()
    assert len(lst) == 5
    assert lst[4] == "Match"
