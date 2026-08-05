"""ShopProcessor — orchestrates per-shop fraud analysis.

Replaces the 200-line ``process_shop()`` function in ``utility.py``.
Each concern is a private helper so they can be tested or overridden independently.

Concurrency:
    The caller (``FraudValidationPipeline``) runs many ``process()`` coroutines
    concurrently via ``asyncio.gather``.  ``process()`` itself is fully async
    and never blocks the event loop (S3 reads are dispatched to the thread-pool
    executor inside ``GeminiService``).
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime

from botocore.exceptions import ClientError

from app.core.models import (
    DetectionResult,
    GpsMetadata,
    ProcessStatus,
    ShopRecord,
    ShopResult,
)
from app.processors.image_processor import ImageProcessor
from app.services.gemini_service import GeminiService
from app.services.s3_service import S3Service
from app.share_log import get_logger

logger = get_logger(__name__)


class ShopProcessor:
    """Processes a single shop: fetch images → SSIM → Gemini → result."""

    def __init__(
        self,
        s3: S3Service,
        gemini: GeminiService,
        image_processor: ImageProcessor,
    ) -> None:
        self._s3 = s3
        self._gemini = gemini
        self._img = image_processor

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process(self, record: ShopRecord, prompt: str) -> ShopResult:
        """Return a fully-populated ``ShopResult`` for *record*.

        Never raises — all errors produce a ``FAIL`` or ``ERROR`` result
        with an appropriate ``complaint_status``.
        """
        def _safe_float(value) -> float:
            try:
                if value in ("", None, "#N/A"):
                    return 0
                return float(value)
            except (ValueError, TypeError):
                return 0
            
        # --- Guard: image count checks ---
        if len(record.image_paths) == 0:
            return ShopResult.no_photo(
                record, 0, 0, _safe_float(record.rtr_lat), _safe_float(record.rtr_lon),
                self._img._gps_flag(0, 0, _safe_float(record.rtr_lat), _safe_float(record.rtr_lon))
            )

        # --- Download images from S3 ---
        images_b64, gps_list = await self._fetch_images(record)
        if not images_b64:
            return ShopResult.s3_error(record)

        # --- Guard: fewer than 3 photos (checked after fetch to get real GPS) ---
        if len(images_b64) < 3:
            gps_ip = gps_list[0]
            return ShopResult.insufficient_photos(
                record,
                gps_ip.photo_lat,
                gps_ip.photo_lon,
                _safe_float(record.rtr_lat),
                _safe_float(record.rtr_lon),
                self._img._gps_flag(gps_ip.photo_lat, gps_ip.photo_lon, _safe_float(record.rtr_lat), _safe_float(record.rtr_lon))
            )
        # --- Build base result ---
        result = self._build_base_result(record, images_b64, gps_list)

        # --- Gemini AI validation ---
        try:
            raw_dict, token_usage = await self._gemini.validate(images_b64, prompt)
        except Exception as exc:
            logger.error(
                f"Gemini call failed for {record.rtr_code}: {exc}"
            )
            result.complaint_status = "inComplaint"
            result.status = ProcessStatus.FAIL
            result.error_message = str(exc)
            result.end_time = datetime.now()
            return result

        detection = DetectionResult.from_dict(raw_dict, token_usage)
        return self._apply_detection(result, detection)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _fetch_images(
        self, record: ShopRecord
    ) -> tuple[list[str], list[GpsMetadata]]:
        """Download images from S3 concurrently and extract EXIF metadata."""
        loop = asyncio.get_event_loop()

        async def _fetch_one(path: str, rtr_lat: str, rtr_lon: str) -> tuple[str, GpsMetadata] | None:
            key = self._s3.normalise_key(path)
            try:
                raw = await loop.run_in_executor(None, lambda: self._s3.read_bytes(key))
                b64 = base64.b64encode(raw).decode("utf-8")
                gps = self._img.extract_metadata(raw, rtr_lat, rtr_lon)
                return b64, gps
            except ClientError as exc:
                if exc.response["Error"]["Code"] == "NoSuchKey":
                    logger.warning(f"S3 key not found: {key}")
                else:
                    logger.warning(f"S3 ClientError for {key}: {exc}")
                return None
            except Exception as exc:
                logger.warning(f"Error fetching image {key}: {exc}")
                return None

        results = await asyncio.gather(*[_fetch_one(p, record.rtr_lat, record.rtr_lon) for p in record.image_paths])
        images_b64: list[str] = []
        gps_list: list[GpsMetadata] = []
        for r in results:
            if r is not None:
                images_b64.append(r[0])
                gps_list.append(r[1])
        return images_b64, gps_list

    def _build_base_result(
        self,
        record: ShopRecord,
        images_b64: list[str],
        gps_list: list[GpsMetadata],
    ) -> ShopResult:
        """Populate photo names, GPS, and SSIM before calling Gemini."""
        paths = record.image_paths
        gps = gps_list[0] if gps_list else GpsMetadata()

        result = ShopResult(
            original_row_id=record.original_row_id,
            run_date=datetime.now().strftime("%Y-%m-%d %H-%M-%S"),
            folder_name=record.shop_path,
            rtr_code=record.rtr_code,
            rtr_name=record.rtr_name,
            number_of_images=len(images_b64),
            photo_name_1=paths[0] if len(paths) > 0 else "",
            photo_name_2=paths[1] if len(paths) > 1 else "",
            photo_name_3=paths[2] if len(paths) > 2 else "",
            photo1_lat=str(gps.photo_lat),
            photo1_long=str(gps.photo_lon),
            rtr1_lat= str(record.rtr_lat), # str(gps.rtr_lat),
            rtr1_long= str(record.rtr_lon), # str(gps.rtr_lon),
            photo1_flag300=gps.flag,
            same_photo=self._img.compute_same_photo_label(images_b64),
            image_parts=list(paths),
            start_time=datetime.now(),
        )
        return result

    def _apply_detection(self, result: ShopResult, detection: DetectionResult) -> ShopResult:
        """Fill AI detection fields and compute Complaint_Status."""
        result.from_other_device = detection.from_other_device
        result.closed_business = detection.shop_operate
        result.un_relate = detection.un_relate
        result.un_relate_human = detection.un_relate_category.un_relate_human
        result.un_relate_animal = detection.un_relate_category.un_relate_animal
        result.un_relate_location = detection.un_relate_category.un_relate_location
        result.un_relate_object = detection.un_relate_category.un_relate_object
        result.token_usage = detection.token_usage
        result.complaint_status = self._classify(result)
        result.status = ProcessStatus.SUCCESS
        result.end_time = datetime.now()
        return result

    @staticmethod
    def _classify(r: ShopResult) -> str:
        """Replicate the original complaint classification logic."""
        indicators = [r.same_photo, r.from_other_device, r.closed_business]
        for indicator in indicators:
            if any(f"{n}/" in indicator for n in ("1", "2", "3")):
                return "inComplaint"
        return "Complaint"
