"""Unit tests for S3Service."""
from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.services.s3_service import S3Service


def _make_client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "test error"}}, "HeadObject")


@pytest.fixture()
def mock_boto_client() -> MagicMock:
    with patch("app.services.s3_service.boto3.client") as mock_factory:
        mock_client = MagicMock()
        mock_factory.return_value = mock_client
        yield mock_client  # type: ignore[misc]


@pytest.fixture()
def svc(mock_boto_client: MagicMock) -> S3Service:
    return S3Service("access_key", "secret_key", "my-bucket")


class TestNormaliseKey:
    def test_strips_s3_prefix(self, svc: S3Service) -> None:
        assert svc.normalise_key("s3://my-bucket/path/to/file.csv") == "path/to/file.csv"

    def test_returns_plain_key_unchanged(self, svc: S3Service) -> None:
        assert svc.normalise_key("path/to/file.csv") == "path/to/file.csv"

    def test_strips_prefix_leaving_empty_string(self, svc: S3Service) -> None:
        assert svc.normalise_key("s3://my-bucket/") == ""

    def test_does_not_strip_different_bucket(self, svc: S3Service) -> None:
        key = "s3://other-bucket/path/file.csv"
        assert svc.normalise_key(key) == key


class TestReadBytes:
    def test_returns_bytes_from_s3(self, svc: S3Service, mock_boto_client: MagicMock) -> None:
        mock_boto_client.get_object.return_value = {"Body": BytesIO(b"hello world")}
        result = svc.read_bytes("some/key.csv")
        assert result == b"hello world"
        mock_boto_client.get_object.assert_called_once_with(Bucket="my-bucket", Key="some/key.csv")

    def test_raises_client_error(self, svc: S3Service, mock_boto_client: MagicMock) -> None:
        mock_boto_client.get_object.side_effect = _make_client_error("NoSuchKey")
        with pytest.raises(ClientError):
            svc.read_bytes("missing/key.csv")


class TestKeyExists:
    def test_returns_true_when_head_succeeds(
        self, svc: S3Service, mock_boto_client: MagicMock
    ) -> None:
        mock_boto_client.head_object.return_value = {}
        assert svc.key_exists("exists/key.csv") is True
        mock_boto_client.head_object.assert_called_once_with(Bucket="my-bucket", Key="exists/key.csv")

    def test_returns_false_on_404(self, svc: S3Service, mock_boto_client: MagicMock) -> None:
        mock_boto_client.head_object.side_effect = _make_client_error("404")
        assert svc.key_exists("missing/key.csv") is False

    def test_reraises_non_404_error(self, svc: S3Service, mock_boto_client: MagicMock) -> None:
        mock_boto_client.head_object.side_effect = _make_client_error("403")
        with pytest.raises(ClientError):
            svc.key_exists("forbidden/key.csv")


class TestReadBytesEncrypt:
    def test_decrypts_and_returns_bytes(
        self, svc: S3Service, mock_boto_client: MagicMock
    ) -> None:
        raw = b"encrypted_data"
        mock_boto_client.get_object.return_value = {"Body": BytesIO(raw)}
        with patch("app.services.s3_service.decrypt_hybrid", return_value=b"decrypted") as mock_decrypt:
            result = svc.read_bytes_encrypt("some/enc.csv", "rsa_private_key")
        assert result == b"decrypted"
        mock_decrypt.assert_called_once()
        # Verify decrypt received a BytesIO wrapping the raw bytes
        call_args = mock_decrypt.call_args[0]
        assert call_args[1] == "rsa_private_key"
