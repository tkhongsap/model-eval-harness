"""EmailComposer — builds the HTML email body for the fraud report.

Extracts all HTML-building logic from ``mail.py``, leaving ``mail.py``
as a thin compatibility shim that calls this class.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

from app.share_log import get_logger

logger = get_logger(__name__)

_THAI_MONTHS = {
    1: "มกราคม", 2: "กุมภาพันธ์", 3: "มีนาคม", 4: "เมษายน",
    5: "พฤษภาคม", 6: "มิถุนายน", 7: "กรกฎาคม", 8: "สิงหาคม",
    9: "กันยายน", 10: "ตุลาคม", 11: "พฤศจิกายน", 12: "ธันวาคม",
}


class EmailComposer:
    """Composes the fraud report email subject and HTML body."""

    def __init__(
        self,
        correct_angel_b64: str,
        confirm_shop_b64: str,
    ) -> None:
        """
        Args:
            correct_angel_b64: Base64 PNG for the correct-angle photo example image.
            confirm_shop_b64:  Base64 PNG for the confirm-shop screenshot image.
        """
        self._angel_b64 = correct_angel_b64
        self._shop_b64 = confirm_shop_b64

    @classmethod
    def from_image_dir(cls, image_dir: str = "app/image") -> EmailComposer:
        """Construct by loading images from the standard directory."""
        base = Path(image_dir)
        return cls(
            correct_angel_b64=base64.b64encode(
                (base / "correct_angel_photo.png").read_bytes()
            ).decode("utf-8"),
            confirm_shop_b64=base64.b64encode(
                (base / "confirm_shop.png").read_bytes()
            ).decode("utf-8"),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compose(
        self,
        today: datetime,
        df: pl.DataFrame,
    ) -> tuple[str, str, dict[str, str]]:
        """Return ``(subject, html_body, inline_images_dict)``.

        ``inline_images_dict`` maps CID name → base64 string, ready to pass
        directly to ``EmailService.send(inline_images=…)``.
        """
        subject = self._build_subject(today)
        html = self._build_body(today, df)
        inline = {
            "correct_angel_photo.png": self._angel_b64,
            "confirm_shop.png": self._shop_b64,
        }
        return subject, html, inline

    # ------------------------------------------------------------------
    # Internal builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_subject(today: datetime) -> str:
        day = str(today.day)
        month_year = today.strftime("%b'%y")
        return (
            f"Retailers/S2 with incompliant photos (by AI) & "
            f"Active Suspicious Retailers/S2 as of {day} {month_year}"
        )

    def _build_body(self, today: datetime, df: pl.DataFrame) -> str:
        yesterday = today - timedelta(days=1)
        thai_year = yesterday.year + 543
        thai_date = f"{yesterday.day} {_THAI_MONTHS[yesterday.month]} {thai_year}"

        html_table1 = self._build_incompliant_table(df)
        html_table2 = self._build_suspicious_table(df)

        return f"""
<p style="margin:0;"><span style="font-size:10pt;">เรียนผู้เกี่ยวข้อง</span></p>
<p style="margin:0;"><span style="font-size:10pt;">อีเมลฉบับนี้จัดทำขึ้นโดยระบบอัตโนมัติ (AI) เพื่อตรวจสอบความถูกต้องของรูปภาพใน Check-in App ตามนโยบายบริษัท โดย AI ตรวจพบว่ามีร้านค้าดังต่อไปนี้ที่รูปถ่าย <b>ไม่เป็นไปตามข้อกำหนด</b></span></p>
<p style="margin:0;"><i><span style="font-size:10pt;">AI ได้ทำการตรวจข้อมูล ในวันที่ {thai_date}</span></i></p>
<p style="margin:0;"><span style="font-size:11pt;">&nbsp;</span></p>
<p style="margin:0;"><span style="font-size:10pt;">ไฟล์แนบ : Retailers with incompliant photos (by AI) &amp; Active suspicious retailers report_{today.strftime('%d%b%y').upper()} :</span></p>
<p style="margin:0;"><span style="font-size:10pt;">Sheet 1) Incompliant Photo Retailer รายการ <b>"ร้านค้าที่รูปถ่าย ไม่เป็นไปตามข้อกำหนด"</b> บน Check-in App และ</span></p>
<p style="margin:0;"><span style="font-size:10pt;">Sheet 2) Suspicious Retailer (Active)&nbsp;รายการ <b>"ร้านค้าต้องสงสัย ที่ยังไม่ได้ยืนยันที่ตั้งร้านค้าโดย PBH"</b></span></p>
<p style="margin:0;"><span style="font-size:11pt;">&nbsp;</span></p>
<p style="margin:0;"><b><span style="font-size:10pt;">1. สรุปข้อมูลจำนวนร้านค้าที่รูปถ่าย ไม่เป็นไปตามข้อกำหนด :</span></b></p>
{html_table1}
<p style="margin:0;"><b><span style="font-size:10pt;">&nbsp;</span></b></p>
<p style="margin:0;"><b><span style="font-size:10pt;" lang="th">การดำเนินการที่ต้องทำ: </span></b><span style="font-size:10pt;" lang="th">ทาง </span><span style="font-size:10pt;">Regional Head <span lang="th">ดำเนินการมอบหมาย </span>PBH <span lang="th">ตรวจสอบและยืนยันการมีอยู่จริงของร้านค้าที่รูปถ่าย ไม่เป็นไปตามข้อกำหนด ดังกล่าว โดยถ่ายรูป/อัปเดทรูปร้านค้า และยืนยันที่ตั้งร้านค้าโดย </span>PBH <span lang="th">บน </span>Check-in App</span></p>
<p style="margin:0;"><span style="font-size:10pt;">&nbsp;</span></p>
<p style="margin:0;"><b><span style="font-size:10pt;">2. <span lang="th">สรุปข้อมูลจำนวน "ร้านค้าต้องสงสัย (</span>Active Suspicious Retailers) <span lang="th">ที่ยังไม่ได้ยืนยันที่ตั้งร้านค้าโดย </span>PBH":</span></b></p>
<p style="margin:0;"><b><span style="font-size:10pt;">&nbsp;</span></b></p>
{html_table2}
<p style="margin:0;"><b><span style="font-size:10pt;">&nbsp;</span></b></p>
<p style="margin:0;"><b><span style="font-size:10pt;" lang="th">การดำเนินการที่ต้องทำ: </span></b><span style="font-size:10pt;" lang="th">ทาง </span><span style="font-size:10pt;">Regional Head <span lang="th">ดำเนินการมอบหมาย </span>PBH <span lang="th">ตรวจสอบและยืนยันการมีอยู่จริงของร้านค้าต้องสงสัย ดังกล่าว โดยถ่ายรูป/อัปเดทรูปร้านค้า และยืนยันที่ตั้งร้านค้าโดย </span>PBH <span lang="th">บน </span>Check-in App</span></p>
<p style="margin:0;"><b><span style="font-size:10pt;">&nbsp;</span></b></p>
<p style="margin:0;"><b><span style="font-size:10pt;">รายละเอียดการดำเนินการ :</span></b></p>
<ul style="margin-top:0;margin-bottom:0;" type="disc">
    <li style="font-size:12pt;font-family:Tahoma,sans-serif;margin:0;">
        <b><span style="font-size:10pt;">กรณีร้านค้ามีอยู่จริง และอยู่ภายใต้การดูแลของท่าน</span></b>
    </li>
    <ul style="margin-top:0;margin-bottom:0;" type="circle">
        <li style="font-size:12pt;font-family:Tahoma,sans-serif;margin:0;">
            <u><span style="font-size:10pt;">ดำเนินการถ่ายรูปร้านค้าใหม่ และอัพเดทรูปร้านค้า</span></u>
            <span style="font-size:10pt;">ดังกล่าว ให้เป็นปัจจุบัน มีความถูกต้องและครบถ้วน ดังนี้</span>
        </li>
        <ul style="margin-top:0;margin-bottom:0;" type="square">
            <li style="font-size:12pt;font-family:Tahoma,sans-serif;margin:0;"><span style="font-size:10pt;">ถ่ายจากสถานที่จริง</span></li>
            <li style="font-size:12pt;font-family:Tahoma,sans-serif;margin:0;"><span style="font-size:10pt;">ภาพชัดเจน ครบทุกมุมมอง</span></li>
            <li style="font-size:12pt;font-family:Tahoma,sans-serif;margin:0;">
                <span style="font-size:10pt;">ถ่าย 3 รูปต่อ 1 ร้านค้า โดยใช้เมนูกล้องถ่ายรูปบน Check-in App เท่านั้น</span>
            </li>
        </ul>
    </ul>
</ul>

<p style="margin-right:0;margin-bottom:0;margin-left:90pt;">
    <span style="font-size:10pt;">
        <img src="cid:correct_angel_photo.png" width="421" height="563" style="width: 315.74pt; height: 422.45pt; cursor: pointer; min-height: auto; min-width: auto;" tabindex="0" crossorigin="use-credentials" fetchpriority="high" class="Do8Zj">
    </span>
</p>
<p style="margin-right:0;margin-bottom:0;margin-left:108pt;"><span style="font-size:10pt;">&nbsp;</span><span style="font-family:Aptos,sans-serif;"></span></p>

<ul type="disc" style="margin-top:0;margin-bottom:0;">
    <ul type="circle" style="margin-top:0;margin-bottom:0;">
        <ul type="square" style="margin-top:0;margin-bottom:0;">
            <li style="font-size:12pt;font-family:Tahoma,sans-serif;margin:0;">
                <span lang="th" style="font-size:10pt;">ยืนยันที่ตั้งร้านค้าโดย </span><span style="font-size:10pt;">PBH</span><span style="font-family:Aptos,sans-serif;"></span>
            </li>
        </ul>
    </ul>
</ul>

<p style="margin-right:0;margin-bottom:0;margin-left:90pt;">
    <span style="font-size:10pt;">
        <img src="cid:confirm_shop.png" width="421" height="422" style="width: 315.74pt; height: 316.14pt; cursor: pointer; min-height: auto; min-width: auto;" tabindex="0" crossorigin="use-credentials" fetchpriority="high" class="Do8Zj">
    </span>
</p>

<p style="margin-right:0;margin-bottom:0;margin-left:36pt;"><span style="font-size:10pt;">&nbsp;</span><span style="font-family:Aptos,sans-serif;"></span></p>
<ul style="margin-top:0;margin-bottom:0;" type="disc">
    <li style="font-size:12pt;font-family:Tahoma,sans-serif;margin:0;">
        <b><span style="font-size:10pt;">กรณีร้านค้าดังกล่าว ไม่มีอยู่จริง หรือไม่ได้อยู่ในความดูแลของท่าน</span></b>
    </li>
    <ul style="margin-top:0;margin-bottom:0;" type="circle">
        <li style="font-size:12pt;font-family:Tahoma,sans-serif;margin:0;">
            <u><span style="font-size:10pt;">ดำเนินการแจ้งปิดร้านค้า</span></u>
            <span style="font-size:10pt;"> : โดยส่งข้อมูลร้านค้าที่ต้องการปิด ให้กับพนักงานฝ่ายสนับสนุน (Admin) ดำเนินการตามขั้นตอนปิดร้านค้า</span>
        </li>
    </ul>
</ul>
<p style="margin:0;">
    <i><span style="font-size:10pt;">&nbsp;&nbsp;&nbsp;(ทีม Channel Member Profile จะดำเนินการปิดร้านค้าทั้งร้านค้าทรูและดีแทคที่เกี่ยวข้องต่อไป)</span></i>
</p>
<p style="margin:0;"><span style="font-size:11pt;">&nbsp;</span></p>
<p style="margin:0;"><span style="font-size:11pt;">&nbsp;</span></p>
<p style="margin:0;">
    <span style="font-size:10pt;">หากไม่พบไฟล์แนบ กรุณาติดต่อ IT Support ทางอีเมล </span>
    <a href="mailto:aioperationsupportteam@truecorp.co.th">
        <span style="color:#467886;font-size:10pt;">aioperationsupportteam@truecorp.co.th</span>
    </a>
    <span style="font-size:10pt;"> เพื่อแจ้งปัญหา</span>
</p>
<p style="margin:0;"><span style="font-size:10pt;">********** อีเมลฉบับนี้ส่งโดยระบบอัตโนมัติ กรุณาอย่าตอบกลับ **********</span></p>
"""

    @staticmethod
    def _build_incompliant_table(df: pl.DataFrame) -> str:
        desired_order = [
            "BMA-East",
            "BMA-West",
            "CW",
            "E",
            "N",
            "NE-1",
            "NE-2",
            "S",
        ]

        order_map = {zone: idx for idx, zone in enumerate(desired_order)}

        zone_names = sorted(
            df.get_column("zone_name").drop_nulls().unique().to_list(),
            key=lambda x: (order_map.get(x, 999), x)
        )
        
        rows = []
        for zone in zone_names:
            zone_df = df.filter(pl.col("zone_name") == zone)

            rows.append(
                {
                    "Region": str(zone),
                    "no_photo_count": "{:,}".format(
                        zone_df.filter(
                            pl.col("Complaint_Status") == "inComplaint-No Photo"
                        ).height
                    ),
                    "no_photo_pbh": "{:,}".format(
                        zone_df.filter(
                            (pl.col("Complaint_Status") == "inComplaint-No Photo")
                            & (pl.col("verified_by_pbh").str.len_chars() != 0)
                        ).height
                    ),
                    "less3_count": "{:,}".format(
                        zone_df.filter(
                            pl.col("Complaint_Status") == "incompliant-Less than 3 Photos"
                        ).height
                    ),
                    "less3_pbh": "{:,}".format(
                        zone_df.filter(
                            (pl.col("Complaint_Status") == "incompliant-Less than 3 Photos")
                            & (pl.col("verified_by_pbh").str.len_chars() != 0)
                        ).height
                    ),
                    "incompliant_count": "{:,}".format(
                        zone_df.filter(
                            pl.col("Complaint_Status") == "inComplaint"
                        ).height
                    ),
                    "incompliant_pbh": "{:,}".format(
                        zone_df.filter(
                            (pl.col("Complaint_Status") == "inComplaint")
                            & (pl.col("verified_by_pbh").str.len_chars() != 0)
                        ).height
                    ),
                }
            )

        # Total row
        rows.append(
            {
                "Region": "Total",
                "no_photo_count": "{:,}".format(
                    df.filter(
                        pl.col("Complaint_Status") == "inComplaint-No Photo"
                    ).height
                ),
                "no_photo_pbh": "{:,}".format(
                    df.filter(
                        (pl.col("Complaint_Status") == "inComplaint-No Photo")
                        & (pl.col("verified_by_pbh").str.len_chars() != 0)
                    ).height
                ),
                "less3_count": "{:,}".format(
                    df.filter(
                        pl.col("Complaint_Status") == "incompliant-Less than 3 Photos"
                    ).height
                ),
                "less3_pbh": "{:,}".format(
                    df.filter(
                        (pl.col("Complaint_Status") == "incompliant-Less than 3 Photos")
                        & (pl.col("verified_by_pbh").str.len_chars() != 0)
                    ).height
                ),
                "incompliant_count": "{:,}".format(
                    df.filter(
                        pl.col("Complaint_Status") == "inComplaint"
                    ).height
                ),
                "incompliant_pbh": "{:,}".format(
                    df.filter(
                        (pl.col("Complaint_Status") == "inComplaint")
                        & (pl.col("verified_by_pbh").str.len_chars() != 0)
                    ).height
                ),
            }
        )

        header = """
<table style="border-spacing:0;border-collapse:collapse;width:auto;">
  <thead>
    <tr style="height:25.15pt;">
        <td valign="top" style="background-color:white;width:105.65pt;height:25.15pt;padding:0 5.4pt;border-style:none none solid none;border-bottom-width:1pt;border-bottom-color:windowtext;"></td>
        <td valign="bottom" colspan="2" style="background-color:white;width:209.35pt;height:25.15pt;padding:0 7.2pt 0 0;border-style:none none solid none;border-bottom-width:1pt;border-bottom-color:windowtext;">
        <p align="center" style="text-align:center;margin:0;"><i><span style="color:black;font-size:13px;">(<span lang="th">ไม่มีรูปถ่าย)</span></span></i></p></td>
        <td valign="bottom" colspan="2" style="background-color:white;width:234pt;height:25.15pt;padding:0 7.2pt 0 0;border-style:none none solid none;border-bottom-width:1pt;border-bottom-color:windowtext;">
        <p align="center" style="text-align:center;margin:0;"><i><span style="color:black;font-size:13px;">(<span lang="th">รูปถ่ายน้อยกว่า </span>3 <span lang="th">รูป)</span></span></i></p></td>
        <td valign="bottom" colspan="2" style="background-color:white;width:252pt;height:25.15pt;padding:0 5.4pt;border-style:none none solid none;border-bottom-width:1pt;border-bottom-color:windowtext;">
        <p align="center" style="text-align:center;margin:0;"><i><span style="color:black;font-size:13px;">(<span lang="th">รูปถ่ายครบ </span>3 <span lang="th">รูป ตรวจสอบรูปมีความผิดปกติ)</span></span></i></p></td>
    </tr>
    <tr style="height:1.0pt">
        <td width="141" valign="top" style="width:105.65pt; border:solid windowtext 1.0pt; border-top:none; background:#0594FF; padding:0in 5.4pt 0in 5.4pt; height:1.0pt"><p style="margin:0in"><b><span style="font-size:13px; color:black">Region</span></b><span style="font-size:13px"></span></p></td>
        <td width="158" valign="top" style="width:118.45pt; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; background:#CAEDFB; padding:0in .1in 0in 0in; height:1.0pt"><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:13px; color:black">#of Retailer/S2</span></b><span style="font-size:13px"></span></p><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:13px; color:black">with Incompliant –</span></b><span style="font-size:13px"></span></p><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:13px; color:black">No photo</span></b><span style="font-size:13px"></span></p></td>
        <td width="121" valign="top" style="width:90.9pt; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; background:#CAEDFB; padding:0in .1in 0in 0in; height:1.0pt"><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:13px; color:black">#of Retailer/S2 PBH Verified</span></b></p></td>
        <td width="180" valign="top" style="width:135.0pt; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; background:#95DCF7; padding:0in .1in 0in 0in; height:1.0pt"><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:13px; color:black">#of Retailer/S2</span></b><span style="font-size:13px"></span></p><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:13px; color:black">with Incompliant –<br>Less than 3 photos</span></b><span style="font-size:13px"></span></p></td>
        <td width="132" valign="top" style="width:99.0pt; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; background:#95DCF7; padding:0in .1in 0in 0in; height:1.0pt"><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:13px; color:black">#of Retailer/S2 PBH Verified</span></b></p></td>
        <td width="192" valign="top" style="width:2.0in; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; background:#60CAF3; padding:0in 5.4pt 0in 5.4pt; height:1.0pt"><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:13px; color:black">#of Retailer/S2</span></b><span style="font-size:13px"></span></p><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:13px; color:black">with Incompliant photo</span></b><span style="font-size:13px"></span></p></td>
        <td width="144" valign="top" style="width:1.5in; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; background:#60CAF3; padding:0in .1in 0in 0in; height:1.0pt"><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:13px; color:black">#of Retailer/S2 PBH Verified</span></b></p></td>
    </tr>  
  </thead>
  <tbody>"""

        body_rows = ""
        for idx, row in enumerate(rows):
            is_last = idx == len(rows) - 1
            w = "font-weight:bold;" if is_last else ""
            body_rows += f"""
    <tr style="height:13.9pt">
        <td width="141" valign="top" style="width:105.65pt; border:solid windowtext 1.0pt; border-top:none; padding:0in 5.4pt 0in 5.4pt; height:13.9pt"><p style="margin:0in"><span style="font-size:13px;{w}">{row["Region"]}</span></p></td>
        <td width="158" valign="top" style="width:118.45pt; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; padding:0in .1in 0in 0in; height:13.9pt"><p align="right" style="margin:0in; text-align:right"><span style="font-size:13px;{w}">{row["no_photo_count"]}</span></p></td>
        <td width="121" valign="top" style="width:90.9pt; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; padding:0in .1in 0in 0in; height:13.9pt"><p align="right" style="margin:0in; text-align:right"><span style="font-size:13px;{w}">{row["no_photo_pbh"]}</span></p></td>
        <td width="180" valign="top" style="width:135.0pt; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; padding:0in .1in 0in 0in; height:13.9pt"><p align="right" style="margin:0in; text-align:right"><span style="font-size:13px;{w}">{row["less3_count"]}</span></p></td>
        <td width="132" valign="top" style="width:99.0pt; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; padding:0in .1in 0in 0in; height:13.9pt"><p align="right" style="margin:0in; text-align:right"><span style="font-size:13px;{w}">{row["less3_pbh"]}</span></p></td>
        <td width="192" valign="top" style="width:2.0in; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; padding:0in 5.4pt 0in 5.4pt; height:13.9pt"><p align="right" style="margin:0in; text-align:right"><span style="font-size:13px;{w}">{row["incompliant_count"]}</span></p></td>
        <td width="144" valign="top" style="width:1.5in; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; padding:0in .1in 0in 0in; height:13.9pt"><p align="right" style="margin:0in; text-align:right"><span style="font-size:13px;{w}">{row["incompliant_pbh"]}</span></p></td>
    </tr>"""

        return header + body_rows + "\n  </tbody>\n</table>"

    @staticmethod
    def _build_suspicious_table(df: pl.DataFrame) -> str:
        desired_order = [
            "BMA-East",
            "BMA-West",
            "CW",
            "E",
            "N",
            "NE-1",
            "NE-2",
            "S",
        ]

        order_map = {zone: idx for idx, zone in enumerate(desired_order)}

        zone_names = sorted(
            df.get_column("zone_name").drop_nulls().unique().to_list(),
            key=lambda x: (order_map.get(x, 999), x)
        )

        rows = []

        for zone in zone_names:
            zone_yes_df = df.filter(
                (pl.col("zone_name") == zone)
                & (pl.col("Suspicious").str.to_lowercase() == "yes")
            )

            suspicious_count = (
                zone_yes_df.height
                - zone_yes_df.filter(
                    pl.col("verified_by_pbh").is_not_null()
                    & (pl.col("verified_by_pbh").cast(pl.Utf8).str.strip_chars() != "")
                ).height
            )

            rows.append(
                {
                    "Region": str(zone),
                    "suspicious": "{:,}".format(suspicious_count),
                }
            )

        # Total row
        yes_df = df.filter(pl.col("Suspicious").str.to_lowercase() == "yes")

        total_suspicious = (
            yes_df.height
            - yes_df.filter(
                pl.col("verified_by_pbh").is_not_null()
                & (pl.col("verified_by_pbh").cast(pl.Utf8).str.strip_chars() != "")
            ).height
        )

        rows.append(
            {
                "Region": "Total",
                "suspicious": "{:,}".format(total_suspicious),
            }
        )

        header = """
<table style="border-spacing:0;border-collapse:collapse;width:auto;">
  <thead>
    <tr>
        <td width="134" valign="top" style="width:100.65pt; border:solid windowtext 1.0pt; background:#0594FF; padding:0in 5.4pt 0in 5.4pt"><p style="margin:0in"><b><span style="font-size:13px; color:black">Region</span></b><span style="font-size:13px"></span></p></td>
        <td width="148" valign="top" style="width:110.85pt; border:solid windowtext 1.0pt; border-left:none; background:#0594FF; padding:0in .1in 0in 0in"><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:13px; color:black">#of Retailer/S2</span></b><span style="font-size:13px"></span></p><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:13px; color:black">in Suspicious Retailer</span></b><span style="font-size:13px"></span></p></td>
    </tr>
  </thead>
  <tbody>"""

        body_rows = ""
        for idx, row in enumerate(rows):
            is_last = idx == len(rows) - 1
            w = "font-weight:bold;" if is_last else ""
            body_rows += f"""
    <tr>
        <td width="134" valign="top" style="width:100.65pt; border:solid windowtext 1.0pt; border-top:none; padding:0in 5.4pt 0in 5.4pt"><p style="margin:0in"><span style="font-size:13px;{w}">{row["Region"]}</span></p></td>
        <td width="148" valign="top" style="width:110.85pt; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; padding:0in .1in 0in 0in"><p align="right" style="margin:0in; text-align:right"><span style="font-size:13px;{w}">{row["suspicious"]}</span></p></td>
    </tr>"""

        return header + body_rows + "\n  </tbody>\n</table>"
