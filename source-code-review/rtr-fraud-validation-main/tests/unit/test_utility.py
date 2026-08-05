"""Unit tests for app/utility.py (legacy module).

Bootstrap strategy
------------------
* test_mail.py installs a lightweight stub for ``app.utility`` to avoid
  import-time side-effects.  Because pytest collects files alphabetically,
  ``test_mail.py`` is collected (and the stub installed) *before* this file.
* We remove the stub here so the *real* module is exercised.
* BATCH_SIZE must be set in ``os.environ`` before the import so the
  module-level ``int(get_secret_value("BATCH_SIZE"))`` succeeds.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest
from botocore.exceptions import ClientError

# ── bootstrap ────────────────────────────────────────────────────────────────
os.environ.setdefault("BATCH_SIZE", "4")
sys.modules.pop("app.utility", None)          # remove stub left by test_mail.py

import app.utility as utility  # noqa: E402  (must come after env/sys setup)


# ── JPEG helpers ─────────────────────────────────────────────────────────────

def _jpeg_bytes(color: tuple[int, int, int] = (128, 64, 32)) -> bytes:
    from PIL import Image
    img = Image.new("RGB", (64, 64), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _b64(color: tuple[int, int, int] = (128, 64, 32)) -> str:
    return base64.b64encode(_jpeg_bytes(color)).decode()


_SHOP_DATA = {"RTR_Code": "RTR001", "RTR_Name": "Test Shop"}


def _info(paths: list[str]) -> dict[str, list[str]]:
    return {"shop_folder": paths}


# ── get_secret_value ─────────────────────────────────────────────────────────

class TestGetSecretValue:
    def test_returns_env_var_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UNIT_TEST_SECRET_XYZ", "hello_world")
        assert utility.get_secret_value("UNIT_TEST_SECRET_XYZ") == "hello_world"

    def test_returns_none_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GHOST_SECRET_XYZ", raising=False)
        assert utility.get_secret_value("GHOST_SECRET_XYZ") is None

    def test_gcp_fallback_on_exception(self) -> None:
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = "gcp_val"
        with (
            patch("os.environ.get", side_effect=RuntimeError("boom")),
            patch("google.auth.default", return_value=(MagicMock(), "proj")),
            patch(
                "google.cloud.secretmanager.SecretManagerServiceClient"
            ) as mock_sm,
        ):
            mock_sm.return_value.access_secret_version.return_value = mock_response
            assert utility.get_secret_value("MY_KEY") == "gcp_val"


# ── get_session_to_s3 ────────────────────────────────────────────────────────

class TestGetSessionToS3:
    def test_returns_client_and_resource(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("S3_AWS_ACCESS_KEY", "fake-key")
        monkeypatch.setenv("S3_AWS_SECRET_KEY", "fake-secret")
        fake_client = MagicMock()
        fake_resource = MagicMock()
        with (
            patch("boto3.client", return_value=fake_client) as mc,
            patch("boto3.resource", return_value=fake_resource) as mr,
        ):
            client, resource = utility.get_session_to_s3()
        assert client is fake_client
        assert resource is fake_resource
        mc.assert_called_once()
        mr.assert_called_once()


# ── get_file_from_s3 ─────────────────────────────────────────────────────────

class TestGetFileFromS3:
    def test_returns_bytes(self) -> None:
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=b"image-data"))
        }
        result = utility.get_file_from_s3(mock_s3, "my-bucket", "some/key.jpg")
        assert result == b"image-data"
        mock_s3.get_object.assert_called_once_with(
            Bucket="my-bucket", Key="some/key.jpg"
        )


# ── save_gemini_response ─────────────────────────────────────────────────────

class TestSaveGeminiResponse:
    def test_saves_plain_dict(self, tmp_path: "Path") -> None:
        path = utility.save_gemini_response({"ok": True}, str(tmp_path / "out"))
        assert path.endswith(".json")
        with open(path) as fh:
            assert json.load(fh) == {"ok": True}

    def test_uses_to_dict_method(self, tmp_path: "Path") -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"key": "val"}
        path = utility.save_gemini_response(mock_resp, str(tmp_path / "out"))
        with open(path) as fh:
            assert json.load(fh)["key"] == "val"

    def test_default_output_dir_key(self) -> None:
        with patch("os.makedirs") as mock_mkdirs, patch("builtins.open", mock_open()):
            utility.save_gemini_response({"x": 1})
        mock_mkdirs.assert_called_once_with("outputs", exist_ok=True)


# ── compare_ssim ─────────────────────────────────────────────────────────────

class TestCompareSsim:
    async def test_identical_image_scores_1(self) -> None:
        b64 = _b64()
        result = await utility.compare_ssim(b64, b64)
        assert result == 1

    async def test_invalid_base64_returns_error_tuple(self) -> None:
        result = await utility.compare_ssim("@@not-b64@@", "@@not-b64@@")
        # Returns (None, error_string) on decode exception
        assert result[0] is None

    async def test_undecodable_image_bytes_returns_error_tuple(self) -> None:
        b64_garbage = base64.b64encode(b"garbage-not-an-image").decode()
        result = await utility.compare_ssim(b64_garbage, b64_garbage)
        # cv2.imdecode returns None → function returns (None, error msg)
        assert result[0] is None

    async def test_returns_int(self) -> None:
        b64a = _b64((255, 0, 0))
        b64b = _b64((0, 0, 255))
        result = await utility.compare_ssim(b64a, b64b)
        assert result in (0, 1)


# ── extract_metadata_from_s3_image ───────────────────────────────────────────

def _mock_img_with_exif(user_comment_bytes: bytes | None = None) -> MagicMock:
    """Build a mock PIL Image context-manager with optional UserComment EXIF."""
    mock_exif = MagicMock()
    mock_exif.items.return_value = [(271, "Canon")]
    if user_comment_bytes is not None:
        mock_exif.get_ifd.return_value = {37510: user_comment_bytes}
    else:
        mock_exif.get_ifd.return_value = {}
    mock_img = MagicMock()
    mock_img.width = 64
    mock_img.height = 64
    mock_img.format = "JPEG"
    mock_img.mode = "RGB"
    mock_img.getexif.return_value = mock_exif
    mock_img.__enter__ = MagicMock(return_value=mock_img)
    mock_img.__exit__ = MagicMock(return_value=False)
    return mock_img


def _gps_comment(
    photo_lat: float = 13.7563,
    photo_lon: float = 100.5019,
    rtr_lat: float = 13.7563,
    rtr_lon: float = 100.5019,
) -> bytes:
    return json.dumps(
        {
            "PHOTO_LATITUDE": str(photo_lat),
            "PHOTO_LONGITUDE": str(photo_lon),
            "RTR_LATITUDE": str(rtr_lat),
            "RTR_LONGITUDE": str(rtr_lon),
        }
    ).encode("utf-8")


class TestExtractMetadataFromS3Image:
    def test_plain_jpeg_returns_fallback_list(self) -> None:
        result = utility.extract_metadata_from_s3_image(_jpeg_bytes())
        assert isinstance(result, list)
        assert len(result) == 5
        assert result[4] == "No Both Lat/Long"

    def test_corrupt_bytes_returns_fallback(self) -> None:
        result = utility.extract_metadata_from_s3_image(b"not-an-image")
        assert result[4] == "No Both Lat/Long"

    def test_returns_five_element_list(self) -> None:
        result = utility.extract_metadata_from_s3_image(_jpeg_bytes())
        assert len(result) == 5

    # ── EXIF UserComment happy paths ─────────────────────────────────────────

    def test_valid_gps_json_same_coords_match(self) -> None:
        comment = _gps_comment(13.7563, 100.5019, 13.7563, 100.5019)
        with patch("app.utility.Image.open", return_value=_mock_img_with_exif(comment)):
            result = utility.extract_metadata_from_s3_image(b"fake")
        assert result[4] == "Match"
        assert result[0] == 13.7563

    def test_distant_coords_returns_not_match(self) -> None:
        # ~11 km apart → Not Match
        comment = _gps_comment(13.7563, 100.5019, 13.7563, 100.6019)
        with patch("app.utility.Image.open", return_value=_mock_img_with_exif(comment)):
            result = utility.extract_metadata_from_s3_image(b"fake")
        assert result[4] == "Not Match"

    def test_zero_photo_coords_returns_no_photo_lat_long(self) -> None:
        comment = _gps_comment(0, 0, 13.7563, 100.5019)
        with patch("app.utility.Image.open", return_value=_mock_img_with_exif(comment)):
            result = utility.extract_metadata_from_s3_image(b"fake")
        assert result[4] == "No Photo Lat/Long"

    def test_zero_rtr_coords_returns_no_checkin_lat_long(self) -> None:
        comment = _gps_comment(13.7563, 100.5019, 0, 0)
        with patch("app.utility.Image.open", return_value=_mock_img_with_exif(comment)):
            result = utility.extract_metadata_from_s3_image(b"fake")
        assert result[4] == "No Checkin Lat/Long"

    def test_all_zero_coords_returns_no_both_lat_long(self) -> None:
        comment = _gps_comment(0, 0, 0, 0)
        with patch("app.utility.Image.open", return_value=_mock_img_with_exif(comment)):
            result = utility.extract_metadata_from_s3_image(b"fake")
        assert result[4] == "No Both Lat/Long"

    def test_unicode_prefix_in_user_comment(self) -> None:
        """UNICODE\x00 prefix is stripped before JSON parsing."""
        payload = _gps_comment(13.7563, 100.5019, 13.7563, 100.5019)
        # Prepend the UNICODE marker (8 bytes total: b"UNICODE" + 1 null byte)
        user_comment = b"UNICODE\x00" + payload
        with patch("app.utility.Image.open", return_value=_mock_img_with_exif(user_comment)):
            result = utility.extract_metadata_from_s3_image(b"fake")
        assert result[4] == "Match"

    def test_no_json_pattern_sets_raw(self) -> None:
        """UserComment bytes without braces → UserComment_Raw in metadata."""
        user_comment = b"plain text no JSON here"
        mock_img = _mock_img_with_exif(user_comment)
        with (
            patch("app.utility.Image.open", return_value=mock_img),
            patch.object(utility, "logger", MagicMock()),
        ):
            result = utility.extract_metadata_from_s3_image(b"fake")
        # Falls through to except (no UserComment_JSON) → fallback
        assert result[4] == "No Both Lat/Long"

    def test_invalid_json_in_user_comment(self) -> None:
        """UserComment with invalid JSON → JSONDecodeError path."""
        user_comment = b'{"broken json'
        mock_img = _mock_img_with_exif(user_comment)
        with (
            patch("app.utility.Image.open", return_value=mock_img),
            patch.object(utility, "logger", MagicMock()),
        ):
            result = utility.extract_metadata_from_s3_image(b"fake")
        assert result[4] == "No Both Lat/Long"

    def test_no_user_comment_tag_returns_fallback(self) -> None:
        """EXIF IFD has no tag 37510 → no GPS extracted → fallback."""
        mock_img = _mock_img_with_exif(None)  # no UserComment
        with patch("app.utility.Image.open", return_value=mock_img):
            result = utility.extract_metadata_from_s3_image(b"fake")
        assert result[4] == "No Both Lat/Long"

    def test_json_decode_error_in_user_comment_sets_raw(self) -> None:
        """UserComment with {bad json} → JSONDecodeError path → UserComment_Raw set."""
        # Must include {} so regex matches, but json.loads fails
        user_comment = b'{invalid: "json", missing_quotes: true}'
        mock_img = _mock_img_with_exif(user_comment)
        with (
            patch("app.utility.Image.open", return_value=mock_img),
            patch.object(utility, "logger", MagicMock()),
        ):
            result = utility.extract_metadata_from_s3_image(b"fake")
        assert result[4] == "No Both Lat/Long"


# ── fraud_validation_task ────────────────────────────────────────────────────

class TestFraudValidationTask:
    def _make_response(self, text: str) -> MagicMock:
        part = MagicMock()
        part.text = text
        content = MagicMock()
        content.parts = [part]
        candidate = MagicMock()
        candidate.content = content
        txt = MagicMock()
        txt.modality = "TEXT"
        txt.token_count = 5
        img_tok = MagicMock()
        img_tok.modality = "IMAGE"
        img_tok.token_count = 10
        usage = MagicMock()
        usage.prompt_tokens_details = [txt, img_tok]
        usage.cache_tokens_details = []
        usage.candidates_token_count = 20
        resp = MagicMock()
        resp.candidates = [candidate]
        resp.usage_metadata = usage
        return resp

    async def test_success_returns_dict_and_meta(self) -> None:
        payload = {"from_other_device": "0/3", "shop_operate": "0/3"}
        resp = self._make_response(f"```json\n{json.dumps(payload)}\n```")
        gemini = MagicMock()
        gemini.models.generate_content.return_value = resp
        result, meta = await utility.fraud_validation_task(gemini, [_b64()], "prompt")
        assert result["from_other_device"] == "0/3"
        assert "text_input_tokens" in meta
        assert "image_input_tokens" in meta

    async def test_empty_response_text_raises_value_error(self) -> None:
        part = MagicMock()
        part.text = None
        content = MagicMock()
        content.parts = [part]
        candidate = MagicMock()
        candidate.content = content
        usage = MagicMock()
        usage.prompt_tokens_details = []
        usage.cache_tokens_details = []
        usage.candidates_token_count = 0
        resp = MagicMock()
        resp.candidates = [candidate]
        resp.usage_metadata = usage
        gemini = MagicMock()
        gemini.models.generate_content.return_value = resp
        # Patch logger to prevent TypeError from custom severity kwarg triggering
        # the tenacity retry (TypeError is in RETRYABLE_EXCEPTIONS).
        with (
            patch.object(utility, "logger", MagicMock()),
            pytest.raises(ValueError, match="empty response"),
        ):
            await utility.fraud_validation_task(gemini, [_b64()], "p")

    async def test_invalid_json_raises_value_error(self) -> None:
        resp = self._make_response('{"bad json here')
        gemini = MagicMock()
        gemini.models.generate_content.return_value = resp
        with pytest.raises(ValueError):
            await utility.fraud_validation_task(gemini, [_b64()], "p")

    async def test_api_exception_raises_value_error(self) -> None:
        gemini = MagicMock()
        gemini.models.generate_content.side_effect = OSError("network down")
        # Patch logger to prevent TypeError from severity kwarg triggering retry.
        with (
            patch.object(utility, "logger", MagicMock()),
            pytest.raises(ValueError, match="Unexpected error"),
        ):
            await utility.fraud_validation_task(gemini, [_b64()], "p")

    async def test_cache_tokens_parsed(self) -> None:
        """Cache token modalities are also parsed (TEXT/IMAGE)."""
        payload = {"from_other_device": "0/3", "shop_operate": "0/3"}
        part = MagicMock()
        part.text = json.dumps(payload)
        content = MagicMock()
        content.parts = [part]
        candidate = MagicMock()
        candidate.content = content
        txt_cache = MagicMock()
        txt_cache.modality = "TEXT"
        txt_cache.token_count = 3
        img_cache = MagicMock()
        img_cache.modality = "IMAGE"
        img_cache.token_count = 7
        usage = MagicMock()
        usage.prompt_tokens_details = []
        usage.cache_tokens_details = [txt_cache, img_cache]
        usage.candidates_token_count = 15
        resp = MagicMock()
        resp.candidates = [candidate]
        resp.usage_metadata = usage
        gemini = MagicMock()
        gemini.models.generate_content.return_value = resp
        _, meta = await utility.fraud_validation_task(gemini, [_b64()], "p")
        assert meta["text_cache_tokens"] == 3
        assert meta["image_cache_tokens"] == 7


# ── process_shop ─────────────────────────────────────────────────────────────

class TestProcessShop:
    def _s3_responses(self, b64_list: list[str]) -> list:
        return [
            {"Body": MagicMock(read=MagicMock(return_value=base64.b64decode(b64)))}
            for b64 in b64_list
        ]

    _GEMINI_RESP = {
        "from_other_device": "0/3",
        "shop_operate": "0/3",
        "un_relate": "0/3",
        "un_relate_category": {
            "un_relate_human": "0",
            "un_relate_animal": "0",
            "un_relate_location": "0",
            "un_relate_object": "0",
        },
    }
    _META = {
        "text_input_tokens": 5,
        "image_input_tokens": 10,
        "text_cache_tokens": 0,
        "image_cache_tokens": 0,
        "output_tokens": 20,
    }

    async def test_no_photos_returns_no_photo_fail(self) -> None:
        row, content = await utility.process_shop(
            _info([]), "prompt", _SHOP_DATA, MagicMock(), "bucket", MagicMock()
        )
        assert content["status"] == "fail"
        assert "No Photo" in row[-2]

    async def test_less_than_3_photos_fail(self) -> None:
        row, content = await utility.process_shop(
            _info(["s3://bucket/a.jpg", "s3://bucket/b.jpg"]),
            "prompt",
            _SHOP_DATA,
            MagicMock(),
            "bucket",
            MagicMock(),
        )
        assert content["status"] == "fail"
        assert "Less than 3" in row[-2]

    async def test_all_s3_reads_fail_returns_no_photo_fail(self) -> None:
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("s3 down")
        row, content = await utility.process_shop(
            _info(["s3://bucket/0.jpg", "s3://bucket/1.jpg", "s3://bucket/2.jpg"]),
            "prompt",
            _SHOP_DATA,
            mock_s3,
            "bucket",
            MagicMock(),
        )
        assert content["status"] == "fail"
        assert row[-1] == "fail"

    async def test_3_images_success(self) -> None:
        imgs = [_b64((c, c, c)) for c in (100, 150, 200)]
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = self._s3_responses(imgs)
        with (
            patch.object(utility, "compare_ssim", new=AsyncMock(return_value=0)),
            patch.object(
                utility,
                "fraud_validation_task",
                new=AsyncMock(return_value=(self._GEMINI_RESP, self._META)),
            ),
        ):
            row, content = await utility.process_shop(
                _info(["s3://bucket/0.jpg", "s3://bucket/1.jpg", "s3://bucket/2.jpg"]),
                "prompt",
                _SHOP_DATA,
                mock_s3,
                "bucket",
                MagicMock(),
            )
        assert content["status"] == "success"
        assert row[-1] == "success"
        assert row[-2] == "Complaint"

    async def test_3_images_incomplaint_when_same_photo(self) -> None:
        """All 3 photos similar → same_photo = '3/3' → inComplaint."""
        imgs = [_b64((100, 100, 100)) for _ in range(3)]
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = self._s3_responses(imgs)
        # compare_ssim returns 1 (similar) for all pairs
        with (
            patch.object(utility, "compare_ssim", new=AsyncMock(return_value=1)),
            patch.object(
                utility,
                "fraud_validation_task",
                new=AsyncMock(return_value=(self._GEMINI_RESP, self._META)),
            ),
        ):
            row, content = await utility.process_shop(
                _info(["s3://bucket/0.jpg", "s3://bucket/1.jpg", "s3://bucket/2.jpg"]),
                "prompt",
                _SHOP_DATA,
                mock_s3,
                "bucket",
                MagicMock(),
            )
        assert content["status"] == "success"
        assert row[-2] == "inComplaint"

    async def test_retryable_exception_returns_none(self) -> None:
        """RETRYABLE_EXCEPTIONS handler has no return → function returns None."""
        imgs = [_b64() for _ in range(3)]
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = self._s3_responses(imgs)
        with (
            patch.object(utility, "compare_ssim", new=AsyncMock(return_value=0)),
            patch.object(
                utility,
                "fraud_validation_task",
                new=AsyncMock(side_effect=RuntimeError("quota")),
            ),
        ):
            result = await utility.process_shop(
                _info(["s3://bucket/0.jpg", "s3://bucket/1.jpg", "s3://bucket/2.jpg"]),
                "prompt",
                _SHOP_DATA,
                mock_s3,
                "bucket",
                MagicMock(),
            )
        assert result is None

    async def test_client_error_no_such_key(self) -> None:
        imgs = [_b64() for _ in range(3)]
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = self._s3_responses(imgs)
        err = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        with (
            patch.object(utility, "compare_ssim", new=AsyncMock(return_value=0)),
            patch.object(
                utility, "fraud_validation_task", new=AsyncMock(side_effect=err)
            ),
        ):
            row, content = await utility.process_shop(
                _info(["s3://bucket/0.jpg", "s3://bucket/1.jpg", "s3://bucket/2.jpg"]),
                "prompt",
                _SHOP_DATA,
                mock_s3,
                "bucket",
                MagicMock(),
            )
        assert content["status"] == "fail"

    async def test_unhandled_exception_returns_incomplaint_fail(self) -> None:
        imgs = [_b64() for _ in range(3)]
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = self._s3_responses(imgs)
        with (
            patch.object(utility, "compare_ssim", new=AsyncMock(return_value=0)),
            patch.object(
                utility,
                "fraud_validation_task",
                new=AsyncMock(side_effect=Exception("unexpected")),
            ),
        ):
            row, content = await utility.process_shop(
                _info(["s3://bucket/0.jpg", "s3://bucket/1.jpg", "s3://bucket/2.jpg"]),
                "prompt",
                _SHOP_DATA,
                mock_s3,
                "bucket",
                MagicMock(),
            )
        assert content["status"] == "fail"
        assert row[-2] == "inComplaint"

    async def test_1_image_same_photo_is_zero_one(self) -> None:
        """1 image loaded → not enough → < 3 images path, returns fail."""
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=_jpeg_bytes()))
        }
        row, content = await utility.process_shop(
            _info(["s3://bucket/0.jpg"]),
            "prompt",
            _SHOP_DATA,
            mock_s3,
            "bucket",
            MagicMock(),
        )
        assert content["status"] == "fail"

    async def test_2_images_loaded_from_3_urls_covers_elif_branch(self) -> None:
        """3 URLs provided; 3rd S3 read fails → image_parts has 2; covers elif==2."""
        first_two = self._s3_responses([_b64((100, 100, 100)), _b64((150, 150, 150))])
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = first_two + [Exception("s3 fail")]
        with (
            patch.object(utility, "compare_ssim", new=AsyncMock(return_value=0)),
            patch.object(
                utility,
                "fraud_validation_task",
                new=AsyncMock(return_value=(self._GEMINI_RESP, self._META)),
            ),
        ):
            row, content = await utility.process_shop(
                _info(["s3://bucket/0.jpg", "s3://bucket/1.jpg", "s3://bucket/2.jpg"]),
                "prompt",
                _SHOP_DATA,
                mock_s3,
                "bucket",
                MagicMock(),
            )
        assert content["status"] == "success"

    async def test_1_image_loaded_from_3_urls_covers_elif_1_branch(self) -> None:
        """3 URLs; only 1st S3 read succeeds → image_parts has 1; covers elif==1."""
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = (
            self._s3_responses([_b64()]) + [Exception("f"), Exception("f")]
        )
        with (
            patch.object(
                utility,
                "fraud_validation_task",
                new=AsyncMock(return_value=(self._GEMINI_RESP, self._META)),
            ),
        ):
            row, content = await utility.process_shop(
                _info(["s3://bucket/0.jpg", "s3://bucket/1.jpg", "s3://bucket/2.jpg"]),
                "prompt",
                _SHOP_DATA,
                mock_s3,
                "bucket",
                MagicMock(),
            )
        assert content["status"] == "success"

    async def test_none_gemini_result_raises_and_returns_fail(self) -> None:
        """fraud_validation_task returning (None, meta) → ValueError → handled."""
        imgs = [_b64() for _ in range(3)]
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = self._s3_responses(imgs)
        with (
            patch.object(utility, "compare_ssim", new=AsyncMock(return_value=0)),
            patch.object(
                utility,
                "fraud_validation_task",
                new=AsyncMock(return_value=(None, self._META)),
            ),
        ):
            row, content = await utility.process_shop(
                _info(["s3://bucket/0.jpg", "s3://bucket/1.jpg", "s3://bucket/2.jpg"]),
                "prompt",
                _SHOP_DATA,
                mock_s3,
                "bucket",
                MagicMock(),
            )
        assert content["status"] == "fail"


# ── send_outlook_graph_api ───────────────────────────────────────────────────

class TestSendOutlookGraphApi:
    async def test_sends_plain_email(self) -> None:
        with (
            patch.object(utility, "get_secret_value", return_value="val"),
            patch("app.utility.ClientSecretCredential"),
            patch("app.utility.GraphServiceClient") as mock_gc,
        ):
            send_mock = AsyncMock()
            mock_gc.return_value.users.by_user_id.return_value.send_mail.post = (
                send_mock
            )
            await utility.send_outlook_graph_api(["to@e.com"], "Subject", "Body")
        send_mock.assert_awaited_once()

    async def test_sends_html_email(self) -> None:
        with (
            patch.object(utility, "get_secret_value", return_value="val"),
            patch("app.utility.ClientSecretCredential"),
            patch("app.utility.GraphServiceClient") as mock_gc,
        ):
            send_mock = AsyncMock()
            mock_gc.return_value.users.by_user_id.return_value.send_mail.post = (
                send_mock
            )
            await utility.send_outlook_graph_api(
                ["to@e.com"], "Subject", "<b>Body</b>", is_html=True
            )
        send_mock.assert_awaited_once()

    async def test_sends_with_attachments(self) -> None:
        fake_b64 = base64.b64encode(b"pdf-bytes").decode()
        with (
            patch.object(utility, "get_secret_value", return_value="val"),
            patch("app.utility.ClientSecretCredential"),
            patch("app.utility.GraphServiceClient") as mock_gc,
        ):
            send_mock = AsyncMock()
            mock_gc.return_value.users.by_user_id.return_value.send_mail.post = (
                send_mock
            )
            await utility.send_outlook_graph_api(
                ["t@e.com"], "S", "B", attachments={"report.pdf": fake_b64}
            )
        send_mock.assert_awaited_once()

    async def test_sends_with_inline_images(self) -> None:
        fake_b64 = base64.b64encode(b"png-bytes").decode()
        with (
            patch.object(utility, "get_secret_value", return_value="val"),
            patch("app.utility.ClientSecretCredential"),
            patch("app.utility.GraphServiceClient") as mock_gc,
        ):
            send_mock = AsyncMock()
            mock_gc.return_value.users.by_user_id.return_value.send_mail.post = (
                send_mock
            )
            await utility.send_outlook_graph_api(
                ["t@e.com"], "S", "B", inline_images={"img.png": fake_b64}
            )
        send_mock.assert_awaited_once()

    async def test_exception_is_re_raised(self) -> None:
        with patch.object(
            utility, "get_secret_value", side_effect=RuntimeError("no secret")
        ):
            with pytest.raises(RuntimeError, match="no secret"):
                await utility.send_outlook_graph_api(["t@e.com"], "s", "b")

    async def test_sends_with_both_attachments_and_inline(self) -> None:
        attach_b64 = base64.b64encode(b"xlsx").decode()
        inline_b64 = base64.b64encode(b"png").decode()
        with (
            patch.object(utility, "get_secret_value", return_value="val"),
            patch("app.utility.ClientSecretCredential"),
            patch("app.utility.GraphServiceClient") as mock_gc,
        ):
            send_mock = AsyncMock()
            mock_gc.return_value.users.by_user_id.return_value.send_mail.post = (
                send_mock
            )
            await utility.send_outlook_graph_api(
                ["t@e.com"],
                "S",
                "B",
                attachments={"file.xlsx": attach_b64},
                inline_images={"chart.png": inline_b64},
            )
        send_mock.assert_awaited_once()


# ── download_images ───────────────────────────────────────────────────────────

class TestDownloadImages:
    async def test_downloads_and_saves(self) -> None:
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=b"img-bytes"))
        }
        info: dict[str, list[str]] = {
            "folder": ["s3://bucket/2023/01/01/img.jpg"]
        }
        with patch("os.makedirs"), patch("builtins.open", mock_open()):
            await utility.download_images("TestShop", info, mock_s3, "bucket")
        mock_s3.get_object.assert_called_once_with(
            Bucket="bucket", Key="2023/01/01/img.jpg"
        )

    async def test_s3_error_silently_swallowed(self) -> None:
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("s3 down")
        info: dict[str, list[str]] = {
            "folder": ["s3://bucket/2023/01/01/img.jpg"]
        }
        # Must not raise
        await utility.download_images("TestShop", info, mock_s3, "bucket")

    async def test_empty_images_list_no_calls(self) -> None:
        mock_s3 = MagicMock()
        await utility.download_images("Shop", {"f": []}, mock_s3, "bucket")
        mock_s3.get_object.assert_not_called()
