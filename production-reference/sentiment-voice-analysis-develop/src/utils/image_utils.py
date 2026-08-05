"""Image-quality scoring utilities.

Generic, self-contained helpers for computing an Image Quality Score (IQS)
on raster pages or single images. The formula::

    IQS = wV * VQ + wS * SQ + wC * CT

Where:
    VQ — visual quality (sharpness / blur)
    SQ — structural quality (skew / orientation)
    CT — content type (foreground density)

Helpers in this module are self-contained — no shared module-level regex
or constant globals; thresholds, weights, and tuning parameters are passed
in by callers via an ``iqs_config`` dict (see *IQS config schema* below).

Heavy CV imports (``cv2``, ``pypdfium2``, ``PIL``) are deferred to inside
the functions so importing this module does not pay the cost when only one
helper is needed.

IQS config schema::

    {
        "weights": {"vq": float, "sq": float, "ct": float},   # must sum to 1.0
        "threshold": float,                                    # overall pass/fail
        "sub_thresholds": {                                    # optional per-component floors
            "vq": float | None,
            "sq": float | None,
            "ct": float | None,
        },
        "visual_quality":    {"blur_min": float, "blur_max": float},
        "structural_quality":{"max_skew_degrees": float},
        "content_type":      {"min_density": float, "max_density": float},
    }

Aggregate result schema (returned by ``score_pdf_chunk`` /
``score_image_bytes``)::

    {
        "iqs": float,                  # minimum per-page IQS in the chunk
        "vq":  float,
        "sq":  float,
        "ct":  float,
        "per_page": list[{"vq": float, "sq": float, "ct": float, "iqs": float}],
        "passed":   bool,              # IQS >= threshold AND all sub-floors met
    }
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np
    from PIL import Image


def rasterize_pdf_pages(pdf_bytes: bytes, dpi: int = 200) -> list[Image.Image]:
    """Rasterize all pages of a PDF into PIL images.

    Args:
        pdf_bytes: Raw PDF bytes.
        dpi: Rasterization DPI. 200 is a sensible default for text-heavy
            documents; raise for fine-detail OCR, lower for thumbnails.

    Returns:
        List of PIL ``Image`` objects, one per page, in document order.

    Raises:
        RuntimeError: If the PDF bytes cannot be opened or rendered.
    """
    import pypdfium2 as pdfium

    doc = None
    try:
        doc = pdfium.PdfDocument(pdf_bytes)
        scale = dpi / 72.0
        return [page.render(scale=scale).to_pil() for page in doc]
    except pdfium.PdfiumError as e:
        raise RuntimeError(f"PDF rasterization failed: {e}") from e
    finally:
        if doc is not None:
            doc.close()


def load_image_bytes(image_bytes: bytes) -> Image.Image:
    """Open raw image bytes (JPEG/PNG/etc.) as a PIL RGB image.

    Args:
        image_bytes: Raw encoded image bytes.

    Returns:
        PIL ``Image`` in ``RGB`` mode.
    """
    import io

    from PIL import Image

    return Image.open(io.BytesIO(image_bytes)).convert("RGB")


def _to_grayscale_array(image: Image.Image) -> np.ndarray:
    """Convert a PIL image to a numpy grayscale (``L``-mode) array."""
    import numpy as np

    return np.asarray(image.convert("L"))


def score_visual_quality(image: Image.Image, blur_min: float, blur_max: float) -> float:
    """Score sharpness vs blur on a 0–1 scale using Laplacian variance.

    Args:
        image: PIL image of a single page.
        blur_min: Laplacian variance at/below which VQ = 0.0 (very blurry).
        blur_max: Laplacian variance at/above which VQ = 1.0 (very sharp).

    Returns:
        Visual-quality score in ``[0.0, 1.0]``. Linear ramp between the
        two bounds.
    """
    import cv2

    gray = _to_grayscale_array(image)
    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if variance <= blur_min:
        return 0.0
    if variance >= blur_max:
        return 1.0
    return (variance - blur_min) / (blur_max - blur_min)


def score_structural_quality(image: Image.Image, max_skew_degrees: float) -> float:
    """Score page skew / orientation on a 0–1 scale.

    Uses the minimum-area rectangle of dark pixels as a coarse skew
    estimator. A perfectly axis-aligned page → ``1.0``; skew at or above
    ``max_skew_degrees`` → ``0.0``.

    Args:
        image: PIL image of a single page.
        max_skew_degrees: Maximum acceptable skew angle in degrees.

    Returns:
        Structural-quality score in ``[0.0, 1.0]``. A blank page returns
        ``1.0`` (it has no skew); content density is judged separately
        by :func:`score_content_type`.
    """
    import cv2
    import numpy as np

    gray = _to_grayscale_array(image)
    # Threshold: ink/text becomes foreground (white) after inversion.
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = np.column_stack(np.nonzero(binary > 0))
    if coords.size == 0:
        return 1.0

    rect_angle = cv2.minAreaRect(coords)[-1]
    # cv2 returns angles in (-90, 0]; normalize to [0, 45].
    angle = abs(rect_angle)
    if angle > 45:
        angle = 90 - angle
    if angle >= max_skew_degrees:
        return 0.0
    return 1.0 - (angle / max_skew_degrees)


def score_content_type(image: Image.Image, min_density: float, max_density: float) -> float:
    """Score how much foreground content the page carries.

    Args:
        image: PIL image of a single page.
        min_density: Below this foreground density the page is considered
            too empty (linear ramp from 0 at density=0 up to 1 at
            ``min_density``).
        max_density: Above this foreground density the page is considered
            too noisy (linear ramp from 1 at ``max_density`` down to 0
            at density=1).

    Returns:
        Content-type score in ``[0.0, 1.0]``. ``1.0`` inside the
        acceptable band ``[min_density, max_density]``.
    """
    import cv2
    import numpy as np

    gray = _to_grayscale_array(image)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    density = float(np.count_nonzero(binary)) / float(binary.size or 1)

    if density < min_density:
        return max(0.0, density / min_density) if min_density > 0 else 0.0
    if density > max_density:
        excess = density - max_density
        room = 1.0 - max_density
        return max(0.0, 1.0 - (excess / room)) if room > 0 else 0.0
    return 1.0


def compute_iqs(vq: float, sq: float, ct: float, weights: dict) -> float:
    """Combine sub-scores into the weighted IQS.

    Args:
        vq: Visual-quality sub-score in ``[0.0, 1.0]``.
        sq: Structural-quality sub-score in ``[0.0, 1.0]``.
        ct: Content-type sub-score in ``[0.0, 1.0]``.
        weights: Mapping with keys ``"vq"``, ``"sq"``, ``"ct"``. The three
            values must sum to 1.0 within a 1e-6 tolerance.

    Returns:
        IQS in ``[0.0, 1.0]``.

    Raises:
        ValueError: If ``weights`` is missing a required key or the
            values do not sum to 1.0.
    """
    for key in ("vq", "sq", "ct"):
        if key not in weights:
            raise ValueError(f"weights missing key: {key!r}")
    total = float(weights["vq"]) + float(weights["sq"]) + float(weights["ct"])
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"IQS weights must sum to 1.0 (got {total})")
    return float(weights["vq"]) * vq + float(weights["sq"]) * sq + float(weights["ct"]) * ct


def score_pdf_chunk(pdf_bytes: bytes, iqs_config: dict) -> dict:
    """Rasterize a PDF, score every page, and return a chunk-level aggregate.

    Args:
        pdf_bytes: Raw PDF bytes for one chunk (one or more pages).
        iqs_config: IQS configuration dict — see *IQS config schema* in
            the module docstring.

    Returns:
        Aggregate result dict — see *Aggregate result schema* in the
        module docstring. The aggregate is the **minimum** per-page
        score (a chunk is only as good as its worst page — one
        unreadable page should not be hidden by averaging).

    Raises:
        RuntimeError: If PDF rasterization fails.
        ValueError: If ``iqs_config["weights"]`` is malformed (see
            :func:`compute_iqs`).
    """
    pages = rasterize_pdf_pages(pdf_bytes)
    per_page = [_score_image(p, iqs_config) for p in pages]
    return _aggregate_chunk_scores(per_page, iqs_config)


def score_pdf_pages(pdf_bytes: bytes, iqs_config: dict) -> list[dict]:
    """Rasterize a PDF and return per-page IQS results — no aggregation.

    Caller filters/aggregates as needed. Used by per-page gating flows that
    want to drop individual bad pages instead of failing whole chunks.

    Args:
        pdf_bytes: Raw PDF bytes (any number of pages).
        iqs_config: IQS configuration dict — see *IQS config schema* in
            the module docstring. Per-component sub-thresholds are honored
            on a per-page basis: each page's ``passed`` reflects the
            overall threshold AND every sub-floor in
            ``iqs_config["sub_thresholds"]``.

    Returns:
        One dict per page in document order, each with keys:
            ``page_no`` (1-indexed within the input PDF),
            ``vq``, ``sq``, ``ct``, ``iqs`` (all floats in ``[0, 1]``),
            ``passed`` (bool — page-level pass/fail).

    Raises:
        RuntimeError: If PDF rasterization fails.
        ValueError: If ``iqs_config["weights"]`` is malformed (see
            :func:`compute_iqs`).
    """
    pages = rasterize_pdf_pages(pdf_bytes)
    threshold = float(iqs_config.get("threshold", 0.6))
    sub_floors = iqs_config.get("sub_thresholds", {}) or {}

    results: list[dict] = []
    for idx, page_image in enumerate(pages, start=1):
        scored = _score_image(page_image, iqs_config)
        passed = scored["iqs"] >= threshold
        if passed:
            for key in ("vq", "sq", "ct"):
                floor = sub_floors.get(key)
                if floor is not None and scored[key] < float(floor):
                    passed = False
                    break
        results.append(
            {
                "page_no": idx,
                "vq": scored["vq"],
                "sq": scored["sq"],
                "ct": scored["ct"],
                "iqs": scored["iqs"],
                "passed": passed,
            }
        )
    return results


def score_image_bytes(image_bytes: bytes, iqs_config: dict) -> dict:
    """Score a single encoded image (JPEG/PNG/etc.) as a one-page chunk.

    Args:
        image_bytes: Raw encoded image bytes.
        iqs_config: IQS configuration dict — see *IQS config schema* in
            the module docstring.

    Returns:
        Aggregate result dict — see *Aggregate result schema* in the
        module docstring.

    Raises:
        ValueError: If ``iqs_config["weights"]`` is malformed.
    """
    image = load_image_bytes(image_bytes)
    per_page = [_score_image(image, iqs_config)]
    return _aggregate_chunk_scores(per_page, iqs_config)


def _score_image(image: Image.Image, iqs_config: dict) -> dict:
    """Score a single PIL image and return per-page sub-scores + IQS."""
    vq_cfg = iqs_config.get("visual_quality", {})
    sq_cfg = iqs_config.get("structural_quality", {})
    ct_cfg = iqs_config.get("content_type", {})
    weights = iqs_config["weights"]

    vq = score_visual_quality(
        image,
        blur_min=float(vq_cfg.get("blur_min", 50.0)),
        blur_max=float(vq_cfg.get("blur_max", 500.0)),
    )
    sq = score_structural_quality(image, max_skew_degrees=float(sq_cfg.get("max_skew_degrees", 8.0)))
    ct = score_content_type(
        image,
        min_density=float(ct_cfg.get("min_density", 0.02)),
        max_density=float(ct_cfg.get("max_density", 0.60)),
    )
    iqs = compute_iqs(vq, sq, ct, weights)
    return {"vq": vq, "sq": sq, "ct": ct, "iqs": iqs}


def _aggregate_chunk_scores(per_page: list[dict], iqs_config: dict) -> dict:
    """Reduce per-page scores to a chunk-level result + pass/fail decision."""
    if not per_page:
        return {"iqs": 0.0, "vq": 0.0, "sq": 0.0, "ct": 0.0, "per_page": [], "passed": False}

    min_page = min(per_page, key=lambda r: r["iqs"])
    threshold = float(iqs_config.get("threshold", 0.6))
    sub_floors = iqs_config.get("sub_thresholds", {}) or {}

    passed = min_page["iqs"] >= threshold
    for key in ("vq", "sq", "ct"):
        floor = sub_floors.get(key)
        if floor is not None and min_page[key] < float(floor):
            passed = False
            break

    return {
        "iqs": min_page["iqs"],
        "vq": min_page["vq"],
        "sq": min_page["sq"],
        "ct": min_page["ct"],
        "per_page": per_page,
        "passed": passed,
    }
