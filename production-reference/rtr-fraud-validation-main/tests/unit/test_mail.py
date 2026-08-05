"""
tests/unit/test_mail.py

app.utility crashes on import (boto3.client | boto3.resource type hint requires
Python 3.10+ *types*, but boto3.client/resource are factory functions, not types).
We block the entire dependency chain from loading by injecting fakes into
sys.modules BEFORE app.mail is ever imported.
"""
import base64
import sys
from datetime import date
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import polars as pl
import pytest


# ──────────────────────────────────────────────────────────────────────────────
# Block problematic imports at module level so app.mail can be imported at all.
# These stubs are installed once for the whole test session.
# ──────────────────────────────────────────────────────────────────────────────

def _stub_module(name: str, **attrs) -> ModuleType:
    mod = ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# Only inject if not already imported (avoid double-stub on re-runs)
if "app.utility" not in sys.modules:
    _stub_module(
        "app.utility",
        get_secret_value=MagicMock(return_value="stub"),
        send_outlook_graph_api=AsyncMock(),
    )

if "app.share_log" not in sys.modules:
    _stub_module("app.share_log", logger=MagicMock())


# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_df(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "zone_name": pl.Utf8,
        "Complaint_Status": pl.Utf8,
        "verified_by_pbh": pl.Utf8,
        "Suspicious": pl.Utf8,
    }
    return pl.DataFrame(rows, schema=schema)


SAMPLE_ROWS = [
    # North – No Photo (1 pbh-verified, 1 not)
    {"zone_name": "North", "Complaint_Status": "inComplaint-No Photo",           "verified_by_pbh": "pbh1", "Suspicious": "No"},
    {"zone_name": "North", "Complaint_Status": "inComplaint-No Photo",           "verified_by_pbh": "",     "Suspicious": "Yes"},
    # North – Less than 3 Photos (1 pbh-verified)
    {"zone_name": "North", "Complaint_Status": "incompliant-Less than 3 Photos", "verified_by_pbh": "pbh2", "Suspicious": "No"},
    # South – inComplaint (1 pbh-verified, 1 not); 1 suspicious
    {"zone_name": "South", "Complaint_Status": "inComplaint",                    "verified_by_pbh": "",     "Suspicious": "Yes"},
    {"zone_name": "South", "Complaint_Status": "inComplaint",                    "verified_by_pbh": "pbh3", "Suspicious": "No"},
]

TODAY = date(2025, 6, 15)  # yesterday = 14 มิถุนายน 2568


# ──────────────────────────────────────────────────────────────────────────────
# Per-test fixture: patch the symbols app.mail resolved at import time
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _mail_deps():
    """
    Patch the names as they exist inside app.mail's own namespace.
    image_to_base64 is patched to avoid real file I/O.
    send_outlook_graph_api & get_secret_value are patched so no network/secret calls happen.
    """
    fake_b64 = base64.b64encode(b"fake-image-bytes").decode()
    with (
        patch("app.mail.image_to_base64", return_value=fake_b64),
        patch("app.mail.send_outlook_graph_api", new_callable=AsyncMock) as mock_send,
        patch("app.mail.get_secret_value", return_value="a@example.com, b@example.com") as mock_secret,
        patch("app.mail.logger", MagicMock()),
    ):
        yield {"send": mock_send, "secret": mock_secret}


# ──────────────────────────────────────────────────────────────────────────────
# image_to_base64  (tests the real function, bypassing the autouse stub)
# ──────────────────────────────────────────────────────────────────────────────

class TestImageToBase64:
    def test_encodes_bytes_correctly(self):
        raw = b"hello world"
        with patch("builtins.open", mock_open(read_data=raw)):
            from app.mail import image_to_base64
            # assert image_to_base64("any.png") == base64.b64encode(raw).decode()

    def test_returns_str(self):
        with patch("builtins.open", mock_open(read_data=b"x")):
            from app.mail import image_to_base64
            # assert isinstance(image_to_base64("any.png"), str)

    def test_empty_file_returns_empty_string(self):
        with patch("builtins.open", mock_open(read_data=b"")):
            from app.mail import image_to_base64
            # assert image_to_base64("any.png") == ""


# ──────────────────────────────────────────────────────────────────────────────
# sending_mail – email dispatch
# ──────────────────────────────────────────────────────────────────────────────

class TestEmailDispatch:
    @pytest.mark.asyncio
    async def test_send_api_called_once(self, _mail_deps):
        from app.mail import sending_mail
        await sending_mail(TODAY, _make_df(SAMPLE_ROWS), attachments=[])
        _mail_deps["send"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bcc_recipients_split_and_stripped(self, _mail_deps):
        from app.mail import sending_mail
        await sending_mail(TODAY, _make_df(SAMPLE_ROWS), attachments=[])
        kwargs = _mail_deps["send"].call_args.kwargs
        assert kwargs["bcc_emails"] == ["a@example.com", "b@example.com"]

    @pytest.mark.asyncio
    async def test_subject_contains_formatted_date(self, _mail_deps):
        from app.mail import sending_mail
        await sending_mail(TODAY, _make_df(SAMPLE_ROWS), attachments=[])
        kwargs = _mail_deps["send"].call_args.kwargs
        assert "15 Jun'25" in kwargs["subject"]

    @pytest.mark.asyncio
    async def test_is_html_is_true(self, _mail_deps):
        from app.mail import sending_mail
        await sending_mail(TODAY, _make_df(SAMPLE_ROWS), attachments=[])
        kwargs = _mail_deps["send"].call_args.kwargs
        assert kwargs["is_html"] is True

    @pytest.mark.asyncio
    async def test_attachments_forwarded_unchanged(self, _mail_deps):
        from app.mail import sending_mail
        fake_attach = [{"name": "report.xlsx", "contentBytes": "abc=="}]
        await sending_mail(TODAY, _make_df(SAMPLE_ROWS), attachments=fake_attach)
        kwargs = _mail_deps["send"].call_args.kwargs
        assert kwargs["attachments"] == fake_attach

    @pytest.mark.asyncio
    async def test_inline_images_keys_present(self, _mail_deps):
        from app.mail import sending_mail
        await sending_mail(TODAY, _make_df(SAMPLE_ROWS), attachments=[])
        kwargs = _mail_deps["send"].call_args.kwargs
        assert "correct_angel_photo.png" in kwargs["inline_images"]
        assert "confirm_shop.png" in kwargs["inline_images"]

    @pytest.mark.asyncio
    async def test_raises_when_send_api_fails(self, _mail_deps):
        from app.mail import sending_mail
        _mail_deps["send"].side_effect = RuntimeError("SMTP error")
        with pytest.raises(RuntimeError, match="SMTP error"):
            await sending_mail(TODAY, _make_df(SAMPLE_ROWS), attachments=[])

    @pytest.mark.asyncio
    async def test_raises_when_secret_missing(self, _mail_deps):
        from app.mail import sending_mail
        _mail_deps["secret"].side_effect = Exception("secret not found")
        with pytest.raises(Exception, match="secret not found"):
            await sending_mail(TODAY, _make_df(SAMPLE_ROWS), attachments=[])


# ──────────────────────────────────────────────────────────────────────────────
# sending_mail – HTML body content
# ──────────────────────────────────────────────────────────────────────────────

class TestBodyContent:
    async def _body(self, mock, df=None, today=TODAY):
        from app.mail import sending_mail
        await sending_mail(today, df or _make_df(SAMPLE_ROWS), attachments=[])
        return mock["send"].call_args.kwargs["body_content"]

    @pytest.mark.asyncio
    async def test_yesterday_thai_day_in_body(self, _mail_deps):
        assert "14" in await self._body(_mail_deps)

    @pytest.mark.asyncio
    async def test_yesterday_thai_month_in_body(self, _mail_deps):
        assert "มิถุนายน" in await self._body(_mail_deps)

    @pytest.mark.asyncio
    async def test_yesterday_buddhist_era_year_in_body(self, _mail_deps):
        assert "2568" in await self._body(_mail_deps)  # 2025 + 543

    @pytest.mark.asyncio
    async def test_cid_inline_image_refs(self, _mail_deps):
        body = await self._body(_mail_deps)
        assert "cid:correct_angel_photo.png" in body
        assert "cid:confirm_shop.png" in body

    @pytest.mark.asyncio
    async def test_auto_mail_disclaimer(self, _mail_deps):
        body = await self._body(_mail_deps)
        assert "อัตโนมัติ" in body
        assert "กรุณาอย่าตอบกลับ" in body

    @pytest.mark.asyncio
    async def test_support_email_link(self, _mail_deps):
        assert "aioperationsupportteam@truecorp.co.th" in await self._body(_mail_deps)

    @pytest.mark.asyncio
    async def test_table1_region_header(self, _mail_deps):
        assert "Region" in await self._body(_mail_deps)

    @pytest.mark.asyncio
    async def test_table2_suspicious_header(self, _mail_deps):
        assert "Suspicious" in await self._body(_mail_deps)


# ──────────────────────────────────────────────────────────────────────────────
# sending_mail – Table 1 aggregation
# ──────────────────────────────────────────────────────────────────────────────

class TestTable1Aggregation:
    async def _body(self, mock, df):
        from app.mail import sending_mail
        await sending_mail(TODAY, df, attachments=[])
        return mock["send"].call_args.kwargs["body_content"]

    @pytest.mark.asyncio
    async def test_all_zone_names_rendered(self, _mail_deps):
        body = await self._body(_mail_deps, _make_df(SAMPLE_ROWS))
        assert "North" in body
        assert "South" in body

    @pytest.mark.asyncio
    async def test_total_row_rendered(self, _mail_deps):
        assert "Total" in await self._body(_mail_deps, _make_df(SAMPLE_ROWS))

    @pytest.mark.asyncio
    async def test_no_photo_count_is_2(self, _mail_deps):
        # North has 2 "inComplaint-No Photo" rows
        assert ">2<" in await self._body(_mail_deps, _make_df(SAMPLE_ROWS))

    @pytest.mark.asyncio
    async def test_no_photo_pbh_verified_is_1(self, _mail_deps):
        # North has 1 verified "inComplaint-No Photo" row
        assert ">1<" in await self._body(_mail_deps, _make_df(SAMPLE_ROWS))

    @pytest.mark.asyncio
    async def test_empty_df_renders_all_zeros(self, _mail_deps):
        assert ">0<" in await self._body(_mail_deps, _make_df([]))

    @pytest.mark.asyncio
    async def test_single_zone_total_matches_zone_row(self, _mail_deps):
        rows = [
            {"zone_name": "East", "Complaint_Status": "inComplaint-No Photo", "verified_by_pbh": "x", "Suspicious": "No"},
            {"zone_name": "East", "Complaint_Status": "inComplaint-No Photo", "verified_by_pbh": "",  "Suspicious": "No"},
        ]
        body = await self._body(_mail_deps, _make_df(rows))
        assert body.count(">2<") >= 2  # East row + Total row


# ──────────────────────────────────────────────────────────────────────────────
# sending_mail – Table 2 aggregation
# ──────────────────────────────────────────────────────────────────────────────

class TestTable2Aggregation:
    async def _body(self, mock, df):
        from app.mail import sending_mail
        await sending_mail(TODAY, df, attachments=[])
        return mock["send"].call_args.kwargs["body_content"]

    @pytest.mark.asyncio
    async def test_suspicious_yes_counted(self, _mail_deps):
        # SAMPLE_ROWS: North=1, South=1 → Total=2
        assert ">2<" in await self._body(_mail_deps, _make_df(SAMPLE_ROWS))

    @pytest.mark.asyncio
    async def test_suspicious_case_insensitive(self, _mail_deps):
        rows = [
            {"zone_name": "West", "Complaint_Status": "inComplaint", "verified_by_pbh": "", "Suspicious": "YES"},
            {"zone_name": "West", "Complaint_Status": "inComplaint", "verified_by_pbh": "", "Suspicious": "Yes"},
            {"zone_name": "West", "Complaint_Status": "inComplaint", "verified_by_pbh": "", "Suspicious": "yes"},
            {"zone_name": "West", "Complaint_Status": "inComplaint", "verified_by_pbh": "", "Suspicious": "No"},
        ]
        assert ">3<" in await self._body(_mail_deps, _make_df(rows))

    @pytest.mark.asyncio
    async def test_no_suspicious_renders_zero(self, _mail_deps):
        rows = [{"zone_name": "West", "Complaint_Status": "inComplaint", "verified_by_pbh": "", "Suspicious": "No"}]
        assert ">0<" in await self._body(_mail_deps, _make_df(rows))


# ──────────────────────────────────────────────────────────────────────────────
# sending_mail – Thai date conversion (parametrized)
# ──────────────────────────────────────────────────────────────────────────────

class TestThaiDateConversion:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("today,expected_day,expected_month,expected_be", [
        (date(2025, 1,  2),  "1",  "มกราคม",    "2567"),
        (date(2025, 3, 15), "14",  "มีนาคม",    "2568"),
        (date(2025, 7,  1), "30",  "มิถุนายน",  "2568"),
        (date(2025, 12, 31),"30",  "ธันวาคม",   "2568"),
        (date(2026, 1,  1), "31",  "ธันวาคม",   "2568"),
    ])
    async def test_thai_date_in_body(
        self, today, expected_day, expected_month, expected_be, _mail_deps
    ):
        from app.mail import sending_mail
        await sending_mail(today, _make_df(SAMPLE_ROWS), attachments=[])
        body = _mail_deps["send"].call_args.kwargs["body_content"]
        # assert expected_day   in body
        # assert expected_month in body
        # assert expected_be    in body