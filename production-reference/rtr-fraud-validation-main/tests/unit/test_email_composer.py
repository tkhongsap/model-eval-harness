"""Unit tests for EmailComposer."""
from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from app.processors.email_composer import EmailComposer, _THAI_MONTHS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FAKE_B64 = base64.b64encode(b"fake_image_bytes").decode()


@pytest.fixture()
def composer() -> EmailComposer:
    return EmailComposer(correct_angel_b64=_FAKE_B64, confirm_shop_b64=_FAKE_B64)


@pytest.fixture()
def sample_df() -> pl.DataFrame:
    """DataFrame with two zones and various complaint/suspicious statuses."""
    return pl.DataFrame(
        {
            "zone_name": ["North", "North", "South", "South"],
            "Complaint_Status": [
                "inComplaint-No Photo",
                "inComplaint",
                "incompliant-Less than 3 Photos",
                "inComplaint",
            ],
            "verified_by_pbh": ["", "pbh1", "", ""],
            "Suspicious": ["no", "YES", "no", "YES"],
        }
    )


@pytest.fixture()
def empty_df() -> pl.DataFrame:
    """Empty DataFrame with required columns."""
    return pl.DataFrame(
        {
            "zone_name": pl.Series([], dtype=pl.Utf8),
            "Complaint_Status": pl.Series([], dtype=pl.Utf8),
            "verified_by_pbh": pl.Series([], dtype=pl.Utf8),
            "Suspicious": pl.Series([], dtype=pl.Utf8),
        }
    )


# ---------------------------------------------------------------------------
# compose()
# ---------------------------------------------------------------------------

class TestCompose:
    def test_returns_subject_html_inline_tuple(
        self, composer: EmailComposer, sample_df: pl.DataFrame
    ) -> None:
        today = datetime(2024, 3, 15)
        subject, html, inline = composer.compose(today, sample_df)
        assert isinstance(subject, str)
        assert isinstance(html, str)
        assert isinstance(inline, dict)

    def test_inline_images_contains_expected_keys(
        self, composer: EmailComposer, sample_df: pl.DataFrame
    ) -> None:
        _, _, inline = composer.compose(datetime(2024, 3, 15), sample_df)
        assert "correct_angel_photo.png" in inline
        assert "confirm_shop.png" in inline

    def test_inline_images_are_base64_strings(
        self, composer: EmailComposer, sample_df: pl.DataFrame
    ) -> None:
        _, _, inline = composer.compose(datetime(2024, 3, 15), sample_df)
        for key, val in inline.items():
            assert isinstance(val, str), f"{key} should be a string"


# ---------------------------------------------------------------------------
# _build_subject()
# ---------------------------------------------------------------------------

class TestBuildSubject:
    def test_contains_day_and_month(self) -> None:
        today = datetime(2024, 6, 5)
        subject = EmailComposer._build_subject(today)
        assert "5" in subject
        assert "Jun'24" in subject

    def test_contains_required_phrase(self) -> None:
        subject = EmailComposer._build_subject(datetime(2024, 1, 1))
        assert "incompliant" in subject.lower() or "Incompliant" in subject


# ---------------------------------------------------------------------------
# _build_body()
# ---------------------------------------------------------------------------

class TestBuildBody:
    def test_contains_thai_date(
        self, composer: EmailComposer, sample_df: pl.DataFrame
    ) -> None:
        today = datetime(2024, 3, 15)
        html = composer._build_body(today, sample_df)
        # yesterday = 2024-03-14, Thai year = 2567, month = มีนาคม
        assert "2567" in html
        assert _THAI_MONTHS[3] in html  # มีนาคม

    def test_contains_thai_year_offset(
        self, composer: EmailComposer, sample_df: pl.DataFrame
    ) -> None:
        today = datetime(2025, 1, 2)
        html = composer._build_body(today, sample_df)
        # yesterday = 2025-01-01, Thai year = 2568
        assert "2568" in html

    def test_body_contains_table_html(
        self, composer: EmailComposer, sample_df: pl.DataFrame
    ) -> None:
        html = composer._build_body(datetime(2024, 3, 15), sample_df)
        assert "<table" in html
        assert "</table>" in html

    def test_body_with_empty_df(
        self, composer: EmailComposer, empty_df: pl.DataFrame
    ) -> None:
        html = composer._build_body(datetime(2024, 3, 15), empty_df)
        # Should not raise; tables should still render with Total row only
        assert "Total" in html


# ---------------------------------------------------------------------------
# _build_incompliant_table()
# ---------------------------------------------------------------------------

class TestBuildIncompliantTable:
    def test_contains_zone_names(self, sample_df: pl.DataFrame) -> None:
        html = EmailComposer._build_incompliant_table(sample_df)
        assert "North" in html
        assert "South" in html

    def test_contains_total_row(self, sample_df: pl.DataFrame) -> None:
        html = EmailComposer._build_incompliant_table(sample_df)
        assert "Total" in html

    def test_total_row_is_bold(self, sample_df: pl.DataFrame) -> None:
        html = EmailComposer._build_incompliant_table(sample_df)
        # Total row should have font-weight:bold style
        total_idx = html.find("Total")
        bold_idx = html.rfind("font-weight:bold", 0, total_idx + 200)
        assert bold_idx != -1

    def test_counts_no_photo_correctly(self, sample_df: pl.DataFrame) -> None:
        html = EmailComposer._build_incompliant_table(sample_df)
        # North has 1 "inComplaint-No Photo"; South has 0
        assert html.count(">1<") >= 1  # at least one cell with value 1

    def test_empty_df_renders_total_only(self, empty_df: pl.DataFrame) -> None:
        html = EmailComposer._build_incompliant_table(empty_df)
        assert "Total" in html
        assert "<table" in html


# ---------------------------------------------------------------------------
# _build_suspicious_table()
# ---------------------------------------------------------------------------

class TestBuildSuspiciousTable:
    def test_contains_zone_names(self, sample_df: pl.DataFrame) -> None:
        html = EmailComposer._build_suspicious_table(sample_df)
        assert "North" in html
        assert "South" in html

    def test_contains_total_row(self, sample_df: pl.DataFrame) -> None:
        html = EmailComposer._build_suspicious_table(sample_df)
        assert "Total" in html

    def test_counts_suspicious_yes_correctly(self, sample_df: pl.DataFrame) -> None:
        html = EmailComposer._build_suspicious_table(sample_df)
        # North has 1 YES, South has 1 YES → Total = 2
        assert ">2<" in html  # total row

    def test_empty_df_renders_total_only(self, empty_df: pl.DataFrame) -> None:
        html = EmailComposer._build_suspicious_table(empty_df)
        assert "Total" in html
        assert "<table" in html


# ---------------------------------------------------------------------------
# from_image_dir() classmethod
# ---------------------------------------------------------------------------

class TestFromImageDir:
    def test_loads_pngs_from_directory(self, tmp_path: Path) -> None:
        angel = tmp_path / "correct_angel_photo.png"
        shop = tmp_path / "confirm_shop.png"
        angel.write_bytes(b"angel_png_data")
        shop.write_bytes(b"shop_png_data")

        composer = EmailComposer.from_image_dir(str(tmp_path))
        assert composer._angel_b64 == base64.b64encode(b"angel_png_data").decode()
        assert composer._shop_b64 == base64.b64encode(b"shop_png_data").decode()

    def test_raises_when_file_missing(self, tmp_path: Path) -> None:
        with pytest.raises((FileNotFoundError, OSError)):
            EmailComposer.from_image_dir(str(tmp_path))
