"""ImageProcessor — pure image computation, no I/O.

Extracts the following from ``utility.py``:
- ``extract_metadata_from_s3_image()``  →  ``extract_metadata()``
- ``compare_ssim()``  →  ``compute_ssim()``  (was wrongly declared async)

All methods accept / return plain Python types so they are straightforward
to unit-test with fixture bytes.
"""
from __future__ import annotations

import base64
import io
import json
import math
import re

import cv2
import numpy as np
from PIL import Image
from PIL.ExifTags import TAGS
from skimage.metrics import structural_similarity as ssim

from app.core.models import GpsMetadata
from app.share_log import get_logger

logger = get_logger(__name__)


class ImageProcessor:
    """Stateless image analysis utilities."""

    SSIM_THRESHOLD: float = 0.8
    DEFAULT_SIZE: tuple[int, int] = (256, 256)

    # ------------------------------------------------------------------
    # SSIM / duplicate detection
    # ------------------------------------------------------------------

    def compute_ssim(
        self,
        b64_a: str,
        b64_b: str,
        size: tuple[int, int] = DEFAULT_SIZE,
    ) -> float:
        """Return SSIM score [0.0, 1.0] between two base64-encoded images.

        Returns ``0.0`` on decode failure.
        """
        try:
            img_a = cv2.imdecode(
                np.frombuffer(base64.b64decode(b64_a), np.uint8), cv2.IMREAD_COLOR
            )
            img_b = cv2.imdecode(
                np.frombuffer(base64.b64decode(b64_b), np.uint8), cv2.IMREAD_COLOR
            )
        except Exception as exc:
            logger.warning(f"SSIM decode error: {exc}")
            return 0.0

        if img_a is None or img_b is None:
            return 0.0

        gray_a = cv2.cvtColor(cv2.resize(img_a, size), cv2.COLOR_BGR2GRAY)
        gray_b = cv2.cvtColor(cv2.resize(img_b, size), cv2.COLOR_BGR2GRAY)
        score, _ = ssim(gray_a, gray_b, full=True)
        return float(score)

    def are_similar(self, score: float) -> bool:
        return score > self.SSIM_THRESHOLD

    def compute_same_photo_label(self, images_b64: list[str]) -> str:
        """Return a ``"x/total"`` label representing duplicated images.

        Mimics the original logic in ``process_shop()``:
        - 1 image  → ``"0/1"``
        - 2 images → compare pair; ``"2/2"`` if similar, ``"0/2"`` otherwise
        - 3 images → compare all three pairs; cap at ``"3/3"``
        """
        n = len(images_b64)
        if n == 0:
            return "0/0"
        if n == 1:
            return "0/1"
        if n == 2:
            count = 2 * int(self.are_similar(self.compute_ssim(images_b64[0], images_b64[1])))
            return f"{count}/{n}"
        # n == 3
        c1 = 2 * int(self.are_similar(self.compute_ssim(images_b64[0], images_b64[1])))
        c2 = 2 * int(self.are_similar(self.compute_ssim(images_b64[1], images_b64[2])))
        c3 = 2 * int(self.are_similar(self.compute_ssim(images_b64[0], images_b64[2])))
        total = min(c1 + c2 + c3, 3)  # cap at 3
        return f"{total}/{n}"

    # ------------------------------------------------------------------
    # EXIF / GPS metadata
    # ------------------------------------------------------------------

    def extract_metadata(self, image_bytes: bytes, rtr_lat: str, rtr_lon: str) -> GpsMetadata:
        """Parse EXIF UserComment JSON and compute GPS distance.

        Returns a ``GpsMetadata`` with ``flag="No Both Lat/Long"`` on any failure.
        """
        def _safe_float(value) -> float:
            try:
                if value in ("", None, "#N/A"):
                    return 0
                return float(value)
            except (ValueError, TypeError):
                return 0
    
        try:
            raw_meta = self._parse_exif(image_bytes)
            user_comment_json: dict = raw_meta.get("UserComment_JSON", {})

            photo_lat = float(user_comment_json.get("PHOTO_LATITUDE", 0))
            photo_lon = float(user_comment_json.get("PHOTO_LONGITUDE", 0))
            rtr_lat = _safe_float(rtr_lat)
            rtr_lon = _safe_float(rtr_lon)

            flag = self._gps_flag(photo_lat, photo_lon, rtr_lat, rtr_lon)
            return GpsMetadata(
                photo_lat=photo_lat,
                photo_lon=photo_lon,
                rtr_lat=rtr_lat,
                rtr_lon=rtr_lon,
                flag=flag,
            )
        except Exception as exc:
            logger.warning(f"extract_metadata failed: {exc}")
            return GpsMetadata()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _gps_flag(
        photo_lat: float, photo_lon: float, rtr_lat: float, rtr_lon: float
    ) -> str:
        if (photo_lat == 0 and photo_lon == 0) and (rtr_lat == 0 and rtr_lon == 0):
            return "No Both Lat/Long"
        if photo_lat == 0 and photo_lon == 0:
            return "No Photo Lat/Long"
        if rtr_lat == 0 and rtr_lon == 0:
            return "No Checkin Lat/Long"
        distance = ImageProcessor._haversine(photo_lat, photo_lon, rtr_lat, rtr_lon)
        return "Match" if distance <= 300 else "Not Match"

    @staticmethod
    def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6_371_000  # metres
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def _parse_exif(image_bytes: bytes) -> dict:
        """Extract raw EXIF metadata including UserComment JSON."""
        metadata: dict = {}
        with Image.open(io.BytesIO(image_bytes)) as img:
            metadata["width"] = img.width
            metadata["height"] = img.height
            metadata["format"] = img.format
            metadata["mode"] = img.mode
            exif_data = img.getexif()

            if not exif_data:
                return metadata

            for tag_id, value in exif_data.items():
                tag_name = TAGS.get(tag_id, f"Tag_{tag_id}")
                if not isinstance(value, bytes):
                    metadata[tag_name] = value

            try:
                exif_ifd = exif_data.get_ifd(0x8769)
                if not (exif_ifd and 37510 in exif_ifd):
                    return metadata

                user_comment: bytes = exif_ifd[37510]
                if not isinstance(user_comment, bytes):
                    return metadata

                comment_bytes = user_comment
                if b"UNICODE" in comment_bytes:
                    pos = comment_bytes.find(b"UNICODE")
                    comment_bytes = comment_bytes[pos + 8:]

                decoded: str | None = None
                encoding_used = "unknown"
                for enc in ("utf-8", "utf-16-le", "utf-16-be", "utf-16"):
                    try:
                        candidate = comment_bytes.decode(enc, errors="strict")
                        if candidate and ("{" in candidate or '"' in candidate):
                            decoded = candidate
                            encoding_used = enc
                            break
                    except Exception:
                        continue

                if not decoded:
                    decoded = comment_bytes.decode("utf-8", errors="ignore")
                    encoding_used = "utf-8 (errors ignored)"

                json_match = re.search(r"\{.*?\}", decoded, flags=re.DOTALL)
                if not json_match:
                    logger.warning("No JSON pattern found in EXIF UserComment")
                    return metadata

                json_str = json_match.group().replace("\x00", "").strip()
                json_data = json.loads(json_str)
                metadata["UserComment_JSON"] = json_data
                metadata["Encoding_Used"] = encoding_used
                for k, v in json_data.items():
                    metadata[f"UC_{k}"] = v

            except Exception as exc:
                logger.error(f"EXIF extraction error: {exc}")

        return metadata
