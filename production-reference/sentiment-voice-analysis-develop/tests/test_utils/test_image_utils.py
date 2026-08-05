"""Tests for src.utils.image_utils — the in-house IQS scoring heuristic."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw, UnidentifiedImageError

from src.utils.image_utils import (
    compute_iqs,
    load_image_bytes,
    rasterize_pdf_pages,
    score_content_type,
    score_image_bytes,
    score_pdf_chunk,
    score_pdf_pages,
    score_structural_quality,
    score_visual_quality,
)


def _make_pdf_bytes(num_pages: int = 1) -> bytes:
    """Build a minimal valid multi-page PDF (blank pages) using pypdfium2."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    for _ in range(num_pages):
        doc.new_page(width=100, height=100)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_png_bytes(image: Image.Image) -> bytes:
    """Encode a PIL image as PNG bytes."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _blank_image(size: int = 100) -> Image.Image:
    """A pure-white blank page — no foreground content, no skew."""
    return Image.new("RGB", (size, size), color=(255, 255, 255))


def _noise_image(size: int = 100, seed: int = 42) -> Image.Image:
    """A sharp, high-frequency image (random noise) — high Laplacian variance."""
    rng = np.random.default_rng(seed)
    noise = rng.integers(0, 256, size=(size, size), dtype=np.uint8)
    return Image.fromarray(noise, mode="L").convert("RGB")


def _rectangle_image(size: int = 100, box_ratio: float = 0.55, rotate_degrees: float = 0.0) -> Image.Image:
    """A white page with a black rectangle, optionally rotated, for skew/density tests."""
    image = Image.new("L", (size, size), color=255)
    box = int(size * box_ratio)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, box, box], fill=0)
    if rotate_degrees:
        image = image.rotate(rotate_degrees, fillcolor=255, expand=False)
    return image.convert("RGB")


def _measure_density(image: Image.Image) -> float:
    """Independently measure foreground density (dark-pixel fraction) via OTSU threshold.

    Mirrors the thresholding approach documented for ``score_content_type`` so
    tests can assert against a precise oracle instead of hardcoded pixel-count
    literals that would be brittle to drawing-geometry rounding.
    """
    import cv2

    gray = np.asarray(image.convert("L"))
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return float(np.count_nonzero(binary)) / float(binary.size or 1)


def _iqs_config(**overrides) -> dict:
    """Baseline IQS config with sane defaults, overridable per test."""
    config = {
        "weights": {"vq": 1 / 3, "sq": 1 / 3, "ct": 1 / 3},
        "threshold": 0.6,
        "sub_thresholds": {"vq": None, "sq": None, "ct": None},
        "visual_quality": {"blur_min": 50.0, "blur_max": 500.0},
        "structural_quality": {"max_skew_degrees": 8.0},
        "content_type": {"min_density": 0.1, "max_density": 0.6},
    }
    config.update(overrides)
    return config


# ---------------------------------------------------------------------------
# rasterize_pdf_pages
# ---------------------------------------------------------------------------


def test_rasterize_pdf_pages_valid_pdf_returns_one_image_per_page():
    # Arrange
    pdf_bytes = _make_pdf_bytes(num_pages=2)

    # Act
    pages = rasterize_pdf_pages(pdf_bytes)

    # Assert
    assert len(pages) == 2
    assert all(isinstance(page, Image.Image) for page in pages)


def test_rasterize_pdf_pages_higher_dpi_produces_larger_image():
    # Arrange
    pdf_bytes = _make_pdf_bytes(num_pages=1)

    # Act
    low_dpi_pages = rasterize_pdf_pages(pdf_bytes, dpi=72)
    high_dpi_pages = rasterize_pdf_pages(pdf_bytes, dpi=144)

    # Assert
    assert high_dpi_pages[0].size[0] == pytest.approx(low_dpi_pages[0].size[0] * 2, abs=2)


def test_rasterize_pdf_pages_invalid_bytes_raises_runtime_error():
    # Arrange
    garbage = b"not a pdf at all"

    # Act / Assert
    with pytest.raises(RuntimeError, match="PDF rasterization failed"):
        rasterize_pdf_pages(garbage)


# ---------------------------------------------------------------------------
# load_image_bytes
# ---------------------------------------------------------------------------


def test_load_image_bytes_valid_png_returns_rgb_image():
    # Arrange
    source = _blank_image(size=64)
    png_bytes = _make_png_bytes(source)

    # Act
    result = load_image_bytes(png_bytes)

    # Assert
    assert result.mode == "RGB"
    assert result.size == (64, 64)


def test_load_image_bytes_grayscale_input_converts_to_rgb():
    # Arrange
    source = Image.new("L", (32, 32), color=128)
    png_bytes = _make_png_bytes(source)

    # Act
    result = load_image_bytes(png_bytes)

    # Assert
    assert result.mode == "RGB"


def test_load_image_bytes_invalid_bytes_raises_unidentified_image_error():
    # Arrange
    garbage = b"definitely not an image"

    # Act / Assert
    with pytest.raises(UnidentifiedImageError):
        load_image_bytes(garbage)


# ---------------------------------------------------------------------------
# score_visual_quality
# ---------------------------------------------------------------------------


def test_score_visual_quality_blank_image_at_or_below_blur_min_returns_zero():
    # Arrange
    image = _blank_image()

    # Act
    score = score_visual_quality(image, blur_min=50.0, blur_max=500.0)

    # Assert
    assert score == 0.0


def test_score_visual_quality_sharp_image_at_or_above_blur_max_returns_one():
    # Arrange
    image = _noise_image()

    # Act
    score = score_visual_quality(image, blur_min=50.0, blur_max=500.0)

    # Assert
    assert score == 1.0


def test_score_visual_quality_mid_range_variance_returns_linear_ramp():
    # Arrange
    import cv2
    from PIL import ImageFilter

    image = _noise_image()
    blurred = image.filter(ImageFilter.GaussianBlur(radius=3))
    gray = np.asarray(blurred.convert("L"))
    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    blur_min, blur_max = 0.0, variance * 2
    expected = (variance - blur_min) / (blur_max - blur_min)

    # Act
    score = score_visual_quality(blurred, blur_min=blur_min, blur_max=blur_max)

    # Assert
    assert score == pytest.approx(expected)
    assert 0.0 < score < 1.0


# ---------------------------------------------------------------------------
# score_structural_quality
# ---------------------------------------------------------------------------


def test_score_structural_quality_blank_image_returns_one():
    # Arrange
    image = _blank_image()

    # Act
    score = score_structural_quality(image, max_skew_degrees=8.0)

    # Assert
    assert score == 1.0


def test_score_structural_quality_axis_aligned_content_returns_one():
    # Arrange
    image = _rectangle_image(rotate_degrees=0.0)

    # Act
    score = score_structural_quality(image, max_skew_degrees=8.0)

    # Assert
    assert score == pytest.approx(1.0)


def test_score_structural_quality_skew_at_or_above_max_returns_zero():
    # Arrange
    image = _rectangle_image(rotate_degrees=30.0)

    # Act
    score = score_structural_quality(image, max_skew_degrees=8.0)

    # Assert
    assert score == 0.0


def test_score_structural_quality_skew_within_max_returns_partial_score():
    # Arrange
    image = _rectangle_image(rotate_degrees=4.0)

    # Act
    score = score_structural_quality(image, max_skew_degrees=8.0)

    # Assert
    assert score == pytest.approx(0.5, abs=0.15)
    assert 0.0 < score < 1.0


# ---------------------------------------------------------------------------
# score_content_type
# ---------------------------------------------------------------------------


def test_score_content_type_blank_image_returns_zero():
    # Arrange
    image = _blank_image()

    # Act
    score = score_content_type(image, min_density=0.1, max_density=0.6)

    # Assert
    assert score == 0.0


def test_score_content_type_below_min_density_returns_partial_score():
    # Arrange — small foreground rectangle, below the 10% density floor.
    image = _rectangle_image(box_ratio=0.22)
    density = _measure_density(image)
    expected = density / 0.1

    # Act
    score = score_content_type(image, min_density=0.1, max_density=0.6)

    # Assert
    assert score == pytest.approx(expected)
    assert 0.0 < score < 1.0


def test_score_content_type_within_band_returns_one():
    # Arrange — ~30% foreground density, inside [0.1, 0.6].
    image = _rectangle_image(box_ratio=0.55)

    # Act
    score = score_content_type(image, min_density=0.1, max_density=0.6)

    # Assert
    assert score == 1.0


def test_score_content_type_above_max_density_returns_partial_score():
    # Arrange — large foreground rectangle, above the 60% density ceiling.
    image = _rectangle_image(box_ratio=0.94)
    density = _measure_density(image)
    excess = density - 0.6
    room = 1.0 - 0.6
    expected = 1.0 - (excess / room)

    # Act
    score = score_content_type(image, min_density=0.1, max_density=0.6)

    # Assert
    assert score == pytest.approx(expected)
    assert 0.0 < score < 1.0


# ---------------------------------------------------------------------------
# compute_iqs
# ---------------------------------------------------------------------------


def test_compute_iqs_weighted_sum_returns_combined_score():
    # Arrange
    weights = {"vq": 0.5, "sq": 0.3, "ct": 0.2}

    # Act
    iqs = compute_iqs(vq=0.8, sq=0.6, ct=0.4, weights=weights)

    # Assert
    assert iqs == pytest.approx(0.8 * 0.5 + 0.6 * 0.3 + 0.4 * 0.2)


def test_compute_iqs_missing_weight_key_raises_value_error():
    # Arrange
    weights = {"vq": 0.5, "ct": 0.5}

    # Act / Assert
    with pytest.raises(ValueError, match="weights missing key"):
        compute_iqs(vq=0.5, sq=0.5, ct=0.5, weights=weights)


def test_compute_iqs_weights_not_summing_to_one_raises_value_error():
    # Arrange
    weights = {"vq": 0.5, "sq": 0.2, "ct": 0.2}

    # Act / Assert
    with pytest.raises(ValueError, match="must sum to 1.0"):
        compute_iqs(vq=0.5, sq=0.5, ct=0.5, weights=weights)


# ---------------------------------------------------------------------------
# score_pdf_chunk
# ---------------------------------------------------------------------------


def test_score_pdf_chunk_real_blank_pdf_returns_expected_aggregate_shape():
    # Arrange
    pdf_bytes = _make_pdf_bytes(num_pages=1)
    config = _iqs_config()

    # Act
    result = score_pdf_chunk(pdf_bytes, config)

    # Assert
    assert set(result.keys()) == {"iqs", "vq", "sq", "ct", "per_page", "passed"}
    assert len(result["per_page"]) == 1
    assert result["sq"] == 1.0  # blank page has no skew


def test_score_pdf_chunk_multi_page_aggregate_uses_minimum_page_iqs(mocker):
    # Arrange
    good_page = _rectangle_image(box_ratio=0.55)
    bad_page = _blank_image()
    mocker.patch(
        "src.utils.image_utils.rasterize_pdf_pages",
        return_value=[good_page, bad_page],
    )
    config = _iqs_config()

    # Act
    result = score_pdf_chunk(b"irrelevant", config)

    # Assert
    assert len(result["per_page"]) == 2
    expected_min = min(result["per_page"], key=lambda r: r["iqs"])
    assert result["iqs"] == expected_min["iqs"]


def test_score_pdf_chunk_sub_threshold_breach_fails_despite_overall_pass(mocker):
    # Arrange — blank page: vq=0, sq=1.0, ct=0.0. Weight sq heavily so the
    # overall IQS clears the threshold, but a ct sub-floor still fails it.
    mocker.patch(
        "src.utils.image_utils.rasterize_pdf_pages",
        return_value=[_blank_image()],
    )
    config = _iqs_config(
        weights={"vq": 0.1, "sq": 0.8, "ct": 0.1},
        threshold=0.6,
        sub_thresholds={"ct": 0.5},
    )

    # Act
    result = score_pdf_chunk(b"irrelevant", config)

    # Assert
    assert result["iqs"] >= config["threshold"]
    assert result["passed"] is False


def test_score_pdf_chunk_no_pages_returns_default_failed_aggregate(mocker):
    # Arrange
    mocker.patch("src.utils.image_utils.rasterize_pdf_pages", return_value=[])
    config = _iqs_config()

    # Act
    result = score_pdf_chunk(b"irrelevant", config)

    # Assert
    assert result == {"iqs": 0.0, "vq": 0.0, "sq": 0.0, "ct": 0.0, "per_page": [], "passed": False}


def test_score_pdf_chunk_malformed_weights_raises_value_error(mocker):
    # Arrange
    mocker.patch(
        "src.utils.image_utils.rasterize_pdf_pages",
        return_value=[_blank_image()],
    )
    config = _iqs_config(weights={"vq": 0.5, "sq": 0.5})

    # Act / Assert
    with pytest.raises(ValueError, match="weights missing key"):
        score_pdf_chunk(b"irrelevant", config)


# ---------------------------------------------------------------------------
# score_pdf_pages
# ---------------------------------------------------------------------------


def test_score_pdf_pages_returns_sequential_page_numbers_and_pass_flags(mocker):
    # Arrange
    good_page = _rectangle_image(box_ratio=0.55)
    bad_page = _blank_image()
    mocker.patch(
        "src.utils.image_utils.rasterize_pdf_pages",
        return_value=[good_page, bad_page],
    )
    config = _iqs_config()

    # Act
    results = score_pdf_pages(b"irrelevant", config)

    # Assert
    assert [r["page_no"] for r in results] == [1, 2]
    assert results[0]["passed"] is True
    assert results[1]["passed"] is False


def test_score_pdf_pages_sub_threshold_breach_fails_individual_page(mocker):
    # Arrange
    mocker.patch(
        "src.utils.image_utils.rasterize_pdf_pages",
        return_value=[_blank_image()],
    )
    config = _iqs_config(
        weights={"vq": 0.1, "sq": 0.8, "ct": 0.1},
        threshold=0.6,
        sub_thresholds={"ct": 0.5},
    )

    # Act
    results = score_pdf_pages(b"irrelevant", config)

    # Assert
    assert results[0]["iqs"] >= config["threshold"]
    assert results[0]["passed"] is False


def test_score_pdf_pages_defaults_used_when_threshold_and_subfloors_absent(mocker):
    # Arrange
    mocker.patch(
        "src.utils.image_utils.rasterize_pdf_pages",
        return_value=[_rectangle_image(box_ratio=0.55)],
    )
    config = {"weights": {"vq": 1 / 3, "sq": 1 / 3, "ct": 1 / 3}}

    # Act
    results = score_pdf_pages(b"irrelevant", config)

    # Assert
    assert results[0]["passed"] == (results[0]["iqs"] >= 0.6)


# ---------------------------------------------------------------------------
# score_image_bytes
# ---------------------------------------------------------------------------


def test_score_image_bytes_valid_png_returns_single_page_aggregate():
    # Arrange
    png_bytes = _make_png_bytes(_rectangle_image(box_ratio=0.55))
    config = _iqs_config()

    # Act
    result = score_image_bytes(png_bytes, config)

    # Assert
    assert len(result["per_page"]) == 1
    assert result["ct"] == 1.0
    assert "passed" in result
    assert "passed" not in result["per_page"][0]


def test_score_image_bytes_malformed_weights_raises_value_error():
    # Arrange
    png_bytes = _make_png_bytes(_blank_image())
    config = _iqs_config(weights={"vq": 0.5, "sq": 0.6, "ct": 0.1})

    # Act / Assert
    with pytest.raises(ValueError, match="must sum to 1.0"):
        score_image_bytes(png_bytes, config)


def test_score_image_bytes_default_sub_configs_used_when_absent():
    # Arrange — omit visual_quality/structural_quality/content_type entirely.
    png_bytes = _make_png_bytes(_rectangle_image(box_ratio=0.55))
    config = {"weights": {"vq": 1 / 3, "sq": 1 / 3, "ct": 1 / 3}, "threshold": 0.5}

    # Act
    result = score_image_bytes(png_bytes, config)

    # Assert
    assert result["passed"] is True
