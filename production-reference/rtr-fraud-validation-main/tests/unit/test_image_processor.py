"""Unit tests for app/processors/image_processor.py."""
from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from app.processors.image_processor import ImageProcessor
from tests.conftest import make_b64_jpeg, make_jpeg_bytes


@pytest.fixture()
def proc() -> ImageProcessor:
    return ImageProcessor()


# ---------------------------------------------------------------------------
# _haversine
# ---------------------------------------------------------------------------

def test_haversine_same_point() -> None:
    dist = ImageProcessor._haversine(13.7563, 100.5018, 13.7563, 100.5018)
    assert dist == pytest.approx(0.0, abs=1e-6)


def test_haversine_known_distance() -> None:
    # ~111 km per degree of latitude
    dist = ImageProcessor._haversine(0.0, 0.0, 1.0, 0.0)
    assert 110_000 < dist < 112_000


def test_haversine_300m_threshold() -> None:
    # Points ~150 m apart should give < 300 m
    dist = ImageProcessor._haversine(13.7563, 100.5018, 13.75765, 100.5018)
    assert dist < 300

    # Points ~600 m apart should give > 300 m
    dist2 = ImageProcessor._haversine(13.7563, 100.5018, 13.7617, 100.5018)
    assert dist2 > 300


# ---------------------------------------------------------------------------
# _gps_flag
# ---------------------------------------------------------------------------

def test_gps_flag_no_both() -> None:
    assert ImageProcessor._gps_flag(0, 0, 0, 0) == "No Both Lat/Long"


def test_gps_flag_no_photo_lat_long() -> None:
    assert ImageProcessor._gps_flag(0, 0, 13.7, 100.5) == "No Photo Lat/Long"


def test_gps_flag_no_checkin_lat_long() -> None:
    assert ImageProcessor._gps_flag(13.7, 100.5, 0, 0) == "No Checkin Lat/Long"


def test_gps_flag_match_same_point() -> None:
    assert ImageProcessor._gps_flag(13.7563, 100.5018, 13.7563, 100.5018) == "Match"


def test_gps_flag_not_match_far_away() -> None:
    # Bangkok vs Chiang Mai — definitely > 300 m
    assert ImageProcessor._gps_flag(13.7563, 100.5018, 18.7883, 98.9853) == "Not Match"


def test_gps_flag_match_within_300m() -> None:
    # ~150 m apart
    assert ImageProcessor._gps_flag(13.7563, 100.5018, 13.75765, 100.5018) == "Match"


# ---------------------------------------------------------------------------
# compute_ssim
# ---------------------------------------------------------------------------

def test_compute_ssim_identical_images(proc: ImageProcessor) -> None:
    b64 = make_b64_jpeg(color=(100, 150, 200))
    score = proc.compute_ssim(b64, b64)
    assert score > 0.99


def test_compute_ssim_different_images(proc: ImageProcessor) -> None:
    b64_red = make_b64_jpeg(color=(255, 0, 0))
    b64_blue = make_b64_jpeg(color=(0, 0, 255))
    score = proc.compute_ssim(b64_red, b64_blue)
    assert score < 0.9


def test_compute_ssim_invalid_b64_returns_zero(proc: ImageProcessor) -> None:
    score = proc.compute_ssim("not-valid-base64!!!", "also-invalid!!!")
    assert score == 0.0


def test_compute_ssim_symmetry(proc: ImageProcessor) -> None:
    b64_a = make_b64_jpeg(color=(200, 100, 50))
    b64_b = make_b64_jpeg(color=(50, 100, 200))
    assert proc.compute_ssim(b64_a, b64_b) == pytest.approx(proc.compute_ssim(b64_b, b64_a), abs=1e-6)


# ---------------------------------------------------------------------------
# are_similar
# ---------------------------------------------------------------------------

def test_are_similar_above_threshold(proc: ImageProcessor) -> None:
    assert proc.are_similar(0.9) is True


def test_are_similar_at_threshold(proc: ImageProcessor) -> None:
    assert proc.are_similar(0.8) is False


def test_are_similar_below_threshold(proc: ImageProcessor) -> None:
    assert proc.are_similar(0.5) is False


# ---------------------------------------------------------------------------
# compute_same_photo_label
# ---------------------------------------------------------------------------

def test_same_photo_label_empty(proc: ImageProcessor) -> None:
    assert proc.compute_same_photo_label([]) == "0/0"


def test_same_photo_label_one_image(proc: ImageProcessor) -> None:
    assert proc.compute_same_photo_label([make_b64_jpeg()]) == "0/1"


def test_same_photo_label_two_identical(proc: ImageProcessor) -> None:
    b64 = make_b64_jpeg(color=(10, 20, 30))
    label = proc.compute_same_photo_label([b64, b64])
    assert label == "2/2"


def test_same_photo_label_two_different(proc: ImageProcessor) -> None:
    b64_a = make_b64_jpeg(color=(255, 0, 0))
    b64_b = make_b64_jpeg(color=(0, 0, 255))
    label = proc.compute_same_photo_label([b64_a, b64_b])
    assert label == "0/2"


def test_same_photo_label_three_identical(proc: ImageProcessor) -> None:
    b64 = make_b64_jpeg(color=(10, 20, 30))
    label = proc.compute_same_photo_label([b64, b64, b64])
    assert label == "3/3"


def test_same_photo_label_three_all_different_formula(proc: ImageProcessor) -> None:
    """When no pair is similar, all three images produce label '0/3'.

    We mock ``are_similar`` so the test is not sensitive to compression artefacts
    or grayscale conversion details — those are already covered by
    ``test_compute_ssim_*`` and ``test_are_similar_*`` above.
    """
    imgs = [make_b64_jpeg(color=c) for c in [(255, 0, 0), (0, 255, 0), (0, 0, 255)]]
    with patch.object(proc, "are_similar", return_value=False):
        label = proc.compute_same_photo_label(imgs)
    assert label == "0/3"


def test_same_photo_label_three_one_pair_similar_formula(proc: ImageProcessor) -> None:
    """When exactly one pair is similar (first pair), label is '2/3'."""
    imgs = [make_b64_jpeg() for _ in range(3)]
    call_num = 0

    def _one_similar(score: float) -> bool:
        nonlocal call_num
        call_num += 1
        return call_num == 1  # only the first pair comparison is similar

    with patch.object(proc, "are_similar", side_effect=_one_similar):
        label = proc.compute_same_photo_label(imgs)
    assert label == "2/3"


def test_same_photo_label_three_all_pairs_similar_caps_at_3(proc: ImageProcessor) -> None:
    """All three pairs similar → cap at 3 → '3/3'."""
    imgs = [make_b64_jpeg() for _ in range(3)]
    with patch.object(proc, "are_similar", return_value=True):
        label = proc.compute_same_photo_label(imgs)
    assert label == "3/3"


# ---------------------------------------------------------------------------
# extract_metadata — fallback behaviour
# ---------------------------------------------------------------------------

def test_extract_metadata_no_exif_returns_default(proc: ImageProcessor, red_jpeg_bytes: bytes) -> None:
    """A plain JPEG without EXIF returns a default GpsMetadata."""
    meta = proc.extract_metadata(red_jpeg_bytes, 0, 0)
    assert meta.flag == "No Both Lat/Long"
    assert meta.photo_lat == 0.0
    assert meta.photo_lon == 0.0


def test_extract_metadata_invalid_bytes_returns_default(proc: ImageProcessor) -> None:
    meta = proc.extract_metadata(b"not an image at all", 0, 0)
    assert meta.flag == "No Both Lat/Long"


# ---------------------------------------------------------------------------
# extract_metadata — _safe_float edge cases (lines 107, 109-110)
# ---------------------------------------------------------------------------

def test_extract_metadata_empty_string_lat_lon(proc: ImageProcessor, red_jpeg_bytes: bytes) -> None:
    """_safe_float returns 0 for empty string rtr_lat/rtr_lon."""
    meta = proc.extract_metadata(red_jpeg_bytes, "", "")
    assert meta.rtr_lat == 0.0
    assert meta.rtr_lon == 0.0


def test_extract_metadata_none_lat_lon(proc: ImageProcessor, red_jpeg_bytes: bytes) -> None:
    """_safe_float returns 0 for None rtr_lat/rtr_lon."""
    meta = proc.extract_metadata(red_jpeg_bytes, None, None)  # type: ignore[arg-type]
    assert meta.rtr_lat == 0.0


def test_extract_metadata_na_string_lat_lon(proc: ImageProcessor, red_jpeg_bytes: bytes) -> None:
    """_safe_float returns 0 for '#N/A' rtr_lat/rtr_lon."""
    meta = proc.extract_metadata(red_jpeg_bytes, "#N/A", "#N/A")
    assert meta.rtr_lat == 0.0


def test_extract_metadata_invalid_float_returns_default(proc: ImageProcessor, red_jpeg_bytes: bytes) -> None:
    """_safe_float returns 0 when float() raises ValueError."""
    meta = proc.extract_metadata(red_jpeg_bytes, "not_a_float", "also_not_a_float")
    assert meta.rtr_lat == 0.0


# ---------------------------------------------------------------------------
# compute_ssim — cv2.imdecode returns None (line 62)
# ---------------------------------------------------------------------------

def test_compute_ssim_returns_zero_when_imdecode_none(proc: ImageProcessor) -> None:
    """When cv2.imdecode returns None (corrupt image), SSIM returns 0.0."""
    with patch("app.processors.image_processor.cv2.imdecode", return_value=None):
        score = proc.compute_ssim(make_b64_jpeg(), make_b64_jpeg())
    assert score == 0.0


# ---------------------------------------------------------------------------
# _parse_exif — full path through UNICODE UserComment (lines 173-223)
# ---------------------------------------------------------------------------

def _make_mock_image_with_exif(user_comment: bytes | None = None) -> MagicMock:
    """Return a mock PIL image configured with optional UserComment EXIF."""
    mock_img = MagicMock()
    mock_img.__enter__ = MagicMock(return_value=mock_img)
    mock_img.__exit__ = MagicMock(return_value=False)
    mock_img.width = 64
    mock_img.height = 64
    mock_img.format = "JPEG"
    mock_img.mode = "RGB"

    mock_exif = MagicMock()
    # Truthy so the `if not exif_data:` branch is skipped
    mock_exif.__bool__ = MagicMock(return_value=True)
    # One normal tag to exercise the tag iteration loop (lines 173-176)
    mock_exif.items.return_value = [(274, 1)]  # Orientation = 1

    if user_comment is not None:
        mock_exif.get_ifd.return_value = {37510: user_comment}
    else:
        mock_exif.get_ifd.return_value = {}  # No UserComment tag

    mock_img.getexif.return_value = mock_exif
    return mock_img


def test_parse_exif_with_unicode_usercomment_json(proc: ImageProcessor) -> None:
    """_parse_exif extracts JSON from a UNICODE-prefixed UserComment."""
    payload = {"PHOTO_LATITUDE": 13.75, "PHOTO_LONGITUDE": 100.5}
    json_str = json.dumps(payload)
    # UNICODE prefix (8 bytes) + UTF-16-LE encoded JSON
    user_comment = b"UNICODE\x00" + json_str.encode("utf-16-le")

    mock_img = _make_mock_image_with_exif(user_comment)
    with patch("app.processors.image_processor.Image.open", return_value=mock_img):
        result = ImageProcessor._parse_exif(b"fake_bytes")

    assert result.get("UserComment_JSON", {}).get("PHOTO_LATITUDE") == 13.75
    assert result.get("UserComment_JSON", {}).get("PHOTO_LONGITUDE") == 100.5


def test_parse_exif_with_plain_utf8_usercomment_json(proc: ImageProcessor) -> None:
    """_parse_exif extracts JSON from a plain UTF-8 UserComment (no UNICODE prefix)."""
    payload = {"PHOTO_LATITUDE": 1.0}
    json_str = json.dumps(payload)
    user_comment = json_str.encode("utf-8")

    mock_img = _make_mock_image_with_exif(user_comment)
    with patch("app.processors.image_processor.Image.open", return_value=mock_img):
        result = ImageProcessor._parse_exif(b"fake_bytes")

    assert result.get("UserComment_JSON", {}).get("PHOTO_LATITUDE") == 1.0


def test_parse_exif_no_usercomment_returns_base_metadata(proc: ImageProcessor) -> None:
    """_parse_exif returns basic metadata when no UserComment tag present."""
    mock_img = _make_mock_image_with_exif(user_comment=None)
    with patch("app.processors.image_processor.Image.open", return_value=mock_img):
        result = ImageProcessor._parse_exif(b"fake_bytes")

    assert result["width"] == 64
    assert result["height"] == 64
    assert "UserComment_JSON" not in result


def test_parse_exif_no_exif_data_returns_minimal(proc: ImageProcessor) -> None:
    """_parse_exif returns minimal metadata when image has no EXIF block."""
    mock_img = MagicMock()
    mock_img.__enter__ = MagicMock(return_value=mock_img)
    mock_img.__exit__ = MagicMock(return_value=False)
    mock_img.width = 32
    mock_img.height = 32
    mock_img.format = "JPEG"
    mock_img.mode = "RGB"
    mock_img.getexif.return_value = None  # No EXIF

    with patch("app.processors.image_processor.Image.open", return_value=mock_img):
        result = ImageProcessor._parse_exif(b"fake_bytes")

    assert result["width"] == 32
    assert "UserComment_JSON" not in result


def test_parse_exif_non_json_usercomment_returns_base_metadata(proc: ImageProcessor) -> None:
    """_parse_exif gracefully handles UserComment with no JSON pattern."""
    user_comment = b"no json here at all"

    mock_img = _make_mock_image_with_exif(user_comment)
    with patch("app.processors.image_processor.Image.open", return_value=mock_img):
        result = ImageProcessor._parse_exif(b"fake_bytes")

    assert "UserComment_JSON" not in result


def test_parse_exif_non_bytes_usercomment_returns_early(proc: ImageProcessor) -> None:
    """_parse_exif returns early when UserComment value is not bytes."""
    mock_img = _make_mock_image_with_exif()
    mock_img.getexif.return_value.__bool__ = MagicMock(return_value=True)
    mock_img.getexif.return_value.items.return_value = []
    mock_img.getexif.return_value.get_ifd.return_value = {37510: "not bytes"}  # str, not bytes

    with patch("app.processors.image_processor.Image.open", return_value=mock_img):
        result = ImageProcessor._parse_exif(b"fake_bytes")

    assert "UserComment_JSON" not in result
