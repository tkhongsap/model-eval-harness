import base64
from datetime import timedelta

import polars as pl

from .share_log import logger
from .utility import (
    get_secret_value,
    send_outlook_graph_api,
)


def image_to_base64(image_path: str) -> str:
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode("utf-8")

# Load images
confirm_shop_b64 = image_to_base64("app/image/confirm_shop.png")
correct_angel_b64 = image_to_base64("app/image/correct_angel_photo.png")

async def sending_mail(today, df, attachments):
    ############################################# Table Data 1 #############################################
    table_data_1 = []
    zone_names = df.get_column("zone_name").unique().to_list()
    for zone_name in zone_names:
        # append row data
        table_data_1.append(
            {
                "Region": str(zone_name),
                "no_photo_count": str(df.filter((pl.col("zone_name") == zone_name) & (pl.col("Complaint_Status") == "inComplaint-No Photo")).height),
                "no_photo_pbh": str(df.filter((pl.col("zone_name") == zone_name) & (pl.col("Complaint_Status") == "inComplaint-No Photo") & (pl.col("verified_by_pbh").str.len_chars() != 0)).height),
                "less3_count": str(df.filter((pl.col("zone_name") == zone_name) & (pl.col("Complaint_Status") == "incompliant-Less than 3 Photos")).height),
                "less3_pbh": str(df.filter((pl.col("zone_name") == zone_name) & (pl.col("Complaint_Status") == "incompliant-Less than 3 Photos") & (pl.col("verified_by_pbh").str.len_chars() != 0)).height),
                "incompliant_count": str(df.filter((pl.col("zone_name") == zone_name) & (pl.col("Complaint_Status") == "inComplaint")).height),
                "incompliant_pbh": str(df.filter((pl.col("zone_name") == zone_name) & (pl.col("Complaint_Status") == "inComplaint") & (pl.col("verified_by_pbh").str.len_chars() != 0)).height),
            }
        )

    # Total
    table_data_1.append(
        {
            "Region": "Total",
            "no_photo_count": str(df.filter(pl.col("Complaint_Status") == "inComplaint-No Photo").height),
            "no_photo_pbh": str(df.filter((pl.col("Complaint_Status") == "inComplaint-No Photo") & (pl.col("verified_by_pbh").str.len_chars() != 0)).height),
            "less3_count": str(df.filter(pl.col("Complaint_Status") == "incompliant-Less than 3 Photos").height),
            "less3_pbh": str(df.filter((pl.col("Complaint_Status") == "incompliant-Less than 3 Photos") & (pl.col("verified_by_pbh").str.len_chars() != 0)).height),
            "incompliant_count": str(df.filter(pl.col("Complaint_Status") == "inComplaint").height),
            "incompliant_pbh": str(df.filter((pl.col("Complaint_Status") == "inComplaint") & (pl.col("verified_by_pbh").str.len_chars() != 0)).height),
        }
    )

    html_table1 = """
    <table style="border-spacing:0; border-collapse:collapse; width:auto;">
        <thead>
            <!-- Group label row -->
            <tr>
                <td style="width:106pt; border-bottom: 1pt solid black;"></td>
                <td colspan="2" style="width:209pt; border-bottom: 1pt solid black; text-align:center; padding: 4px;"><i>(ไม่มีรูปถ่าย)</i></td>
                <td colspan="2" style="width:234pt; border-bottom: 1pt solid black; text-align:center; padding: 4px;"><i>(รูปถ่ายน้อยกว่า 3 รูป)</i></td>
                <td colspan="2" style="width:252pt; border-bottom: 1pt solid black; text-align:center; padding: 4px;"><i>(รูปถ่ายครบ 3 รูป ตรวจสอบรูปมีความผิดปกติ)</i></td>
            </tr>
            <!-- Column header row -->
            <tr style="height:1.0pt">
                <td width="141" valign="top" style="width:105.65pt; border:solid windowtext 1.0pt; border-top:none; background:#0594FF; padding:0in 5.4pt 0in 5.4pt; height:1.0pt"><p style="margin:0in"><b><span style="font-size:10.0pt; color:black">Region</span></b><span style="font-size:10.0pt"></span></p></td>
                <td width="158" valign="top" style="width:118.45pt; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; background:#CAEDFB; padding:0in .1in 0in 0in; height:1.0pt"><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:10.0pt; color:black">#of Retailer/S2</span></b><span style="font-size:10.0pt"></span></p><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:10.0pt; color:black">with Incompliant –</span></b><span style="font-size:10.0pt"></span></p><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:10.0pt; color:black">No photo</span></b><span style="font-size:10.0pt"></span></p></td>
                <td width="121" valign="top" style="width:90.9pt; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; background:#CAEDFB; padding:0in .1in 0in 0in; height:1.0pt"><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:10.0pt; color:black">#of Retailer/S2 PBH Verified</span></b></p></td>
                <td width="180" valign="top" style="width:135.0pt; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; background:#95DCF7; padding:0in .1in 0in 0in; height:1.0pt"><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:10.0pt; color:black">#of Retailer/S2</span></b><span style="font-size:10.0pt"></span></p><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:10.0pt; color:black">with Incompliant –<br>Less than 3 photos</span></b><span style="font-size:10.0pt"></span></p></td>
                <td width="132" valign="top" style="width:99.0pt; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; background:#95DCF7; padding:0in .1in 0in 0in; height:1.0pt"><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:10.0pt; color:black">#of Retailer/S2 PBH Verified</span></b></p></td>
                <td width="192" valign="top" style="width:2.0in; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; background:#60CAF3; padding:0in 5.4pt 0in 5.4pt; height:1.0pt"><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:10.0pt; color:black">#of Retailer/S2</span></b><span style="font-size:10.0pt"></span></p><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:10.0pt; color:black">with Incompliant photo</span></b><span style="font-size:10.0pt"></span></p></td>
                <td width="144" valign="top" style="width:1.5in; border-top:none; border-left:none; border-bottom:solid windowtext 1.0pt; border-right:solid windowtext 1.0pt; background:#60CAF3; padding:0in .1in 0in 0in; height:1.0pt"><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:10.0pt; color:black">#of Retailer/S2 PBH Verified</span></b></p></td>
            </tr>   
        </thead>
        <tbody>
    """

    for idx, row in enumerate(table_data_1):
        is_last = idx == len(table_data_1) - 1
        weight = "font-weight:bold;" if is_last else ""

        html_table1 += f"""
            <tr>
                <td style="padding:6px; border:1pt solid black; {weight}">{row["Region"]}</td>
                <td style="padding:6px; border:1pt solid black; text-align:right; {weight}">{row["no_photo_count"]}</td>
                <td style="padding:6px; border:1pt solid black; text-align:right; {weight}">{row["no_photo_pbh"]}</td>
                <td style="padding:6px; border:1pt solid black; text-align:right; {weight}">{row["less3_count"]}</td>
                <td style="padding:6px; border:1pt solid black; text-align:right; {weight}">{row["less3_pbh"]}</td>
                <td style="padding:6px; border:1pt solid black; text-align:right; {weight}">{row["incompliant_count"]}</td>
                <td style="padding:6px; border:1pt solid black; text-align:right; {weight}">{row["incompliant_pbh"]}</td>
            </tr>
        """

    html_table1 += """
        </tbody>
    </table>
    """

    ############################################# Table Data 2 #############################################
    table_data_2 = []
    for zone_name in zone_names:
        # append row data
        table_data_2.append(
            {
                "Region": str(zone_name),
                "suspicious": str(df.filter((pl.col("zone_name") == zone_name) & (pl.col("Suspicious").str.to_lowercase() == "yes")).height),
            }
        )

    table_data_2.append(
            {
                "Region": "Total",
                "suspicious": str(df.filter(pl.col("Suspicious").str.to_lowercase() == "yes").height),
            }
        )

    html_table2 = """
    <table style="border-spacing:0; border-collapse:collapse; width:auto;">
        <thead>
            <tr>
                <td width="134" valign="top" style="width:100.65pt; border:solid windowtext 1.0pt; background:#0594FF; padding:0in 5.4pt 0in 5.4pt"><p style="margin:0in"><b><span style="font-size:10.0pt; color:black">Region</span></b><span style="font-size:10.0pt"></span></p></td>
                <td width="148" valign="top" style="width:110.85pt; border:solid windowtext 1.0pt; border-left:none; background:#0594FF; padding:0in .1in 0in 0in"><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:10.0pt; color:black">#of Retailer/S2</span></b><span style="font-size:10.0pt"></span></p><p align="center" style="margin:0in; text-align:center"><b><span style="font-size:10.0pt; color:black">in Suspicious Retailer</span></b><span style="font-size:10.0pt"></span></p></td>
            </tr>
        </thead>
        <tbody>
    """

    for idx, row in enumerate(table_data_2):
        is_last = idx == len(table_data_2) - 1
        weight = "font-weight:bold;" if is_last else ""

        html_table2 += f"""
            <tr>
                <td style="padding:6px; border:1pt solid black; {weight}">{row["Region"]}</td>
                <td style="padding:6px; border:1pt solid black; text-align:right; {weight}">{row["suspicious"]}</td>
            </tr>
        """

    html_table2 += """
        </tbody>
    </table>
    """

    ############################################# Body Content #############################################
    yesterday =  today - timedelta(days=1)
    thai_year = yesterday.year + 543
    thai_months = {
        1: "มกราคม", 2: "กุมภาพันธ์", 3: "มีนาคม", 4: "เมษายน",
        5: "พฤษภาคม", 6: "มิถุนายน", 7: "กรกฎาคม", 8: "สิงหาคม",
        9: "กันยายน", 10: "ตุลาคม", 11: "พฤศจิกายน", 12: "ธันวาคม"
    }

    body_content_html = f"""
    <p style="margin:0;"><span style="font-size:10pt;">เรียนผู้เกี่ยวข้อง</span></p>
    <p style="margin:0;"><span style="font-size:10pt;">อีเมลฉบับนี้จัดทำขึ้นโดยระบบอัตโนมัติ (AI) เพื่อตรวจสอบความถูกต้องของรูปภาพใน Check-in App ตามนโยบายบริษัท โดย AI ตรวจพบว่ามีร้านค้าดังต่อไปนี้ที่รูปถ่าย <b>ไม่เป็นไปตามข้อกำหนด</b></span></p>
    <p style="margin:0;"><i><span style="font-size:10pt;">AI ได้ทำการตรวจข้อมูล ในวันที่ {yesterday.day} {thai_months[yesterday.month]} {thai_year}</span></i></p>
    <p style="margin:0;"><span style="font-size:11pt;">&nbsp;</span></p>
    <p style="margin:0;"><span style="font-size:10pt;">ไฟล์แนบ : Retailers with incompliant photos (by AI) &amp; Active suspicious retailers report_{today.strftime('%d%b%y').upper()} :</span></p>
    <p style="margin:0;"><span style="font-size:10pt;">Sheet 1) Incompliant Photo Retailer รายการ <b>"ร้านค้าที่รูปถ่าย ไม่เป็นไปตามข้อกำหนด"</b> บน Check-in App และ</span></p>
    <p style="margin:0;"><span style="font-size:10pt;">Sheet 2) Suspicious Retailer (Active)&nbsp;รายการ <b>"ร้านค้าต้องสงสัย ที่ยังไม่ได้ยืนยันที่ตั้งร้านค้าโดย PBH"</b></span></p>
    <p style="margin:0;"><span style="font-size:11pt;">&nbsp;</span></p>
    <p style="margin:0;"><b><span style="font-size:10pt;">1. สรุปข้อมูลจำนวนร้านค้าที่รูปถ่าย ไม่เป็นไปตามข้อกำหนด :</span></b></p>
    {html_table1}
    <p style="margin:0;"><b><span style="font-size:10pt;">&nbsp;</span></b><span style="font-family:Aptos,sans-serif;"></span></p>
    <p style="margin:0;"><b><span style="font-size:10pt;" lang="th">การดำเนินการที่ต้องทำ: </span></b><span style="font-size:10pt;" lang="th">ทาง </span><span style="font-size:10pt;">Regional Head <span lang="th">ดำเนินการมอบหมาย </span>PBH <span lang="th">ตรวจสอบและยืนยันการมีอยู่จริงของร้านค้าที่รูปถ่าย ไม่เป็นไปตามข้อกำหนด ดังกล่าว โดยถ่ายรูป/อัปเดทรูปร้านค้า และยืนยันที่ตั้งร้านค้าโดย </span>PBH <span lang="th">บน </span>Check-in App</span><span style="font-family:Aptos,sans-serif;"></span></p>
    <p style="margin:0;"><span style="font-size:10pt;">&nbsp;</span><span style="font-family:Aptos,sans-serif;"></span></p>
    <p style="margin:0;"><b><span style="font-size:10pt;">2. <span lang="th">สรุปข้อมูลจำนวน “ร้านค้าต้องสงสัย (</span>Active Suspicious Retailers) <span lang="th">ที่ยังไม่ได้ยืนยันที่ตั้งร้านค้าโดย </span>PBH (PBH Verified <span lang="th">ผ่าน </span>Check In Application)”:</span></b><span style="font-family:Aptos,sans-serif;"></span></p>
    <p style="margin:0;"><b><span style="font-size:10pt;">&nbsp;</span></b><span style="font-family:Aptos,sans-serif;"></span></p>
    {html_table2}
    <p style="margin:0;"><b><span style="font-size:10pt;">&nbsp;</span></b><span style="font-family:Aptos,sans-serif;"></span></p>
    <p style="margin:0;"><b><span style="font-size:10pt;" lang="th">การดำเนินการที่ต้องทำ: </span></b><span style="font-size:10pt;" lang="th">ทาง </span><span style="font-size:10pt;">Regional Head <span lang="th">ดำเนินการมอบหมาย </span>PBH <span lang="th">ตรวจสอบและยืนยันการมีอยู่จริงของร้านค้าต้องสงสัย ดังกล่าว โดยถ่ายรูป/อัปเดทรูปร้านค้า และยืนยันที่ตั้งร้านค้าโดย </span>PBH <span lang="th">บน </span>Check-in App</span><span style="font-family:Aptos,sans-serif;"></span></p>
    <p style="margin:0;"><b><span style="font-size:10pt;">&nbsp;</span></b><span style="font-family:Aptos,sans-serif;"></span></p>
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
        <img src="cid:correct_angel_photo.png" style="width:317pt; height:auto;">
    </p>

    <ul style="margin-top:0;margin-bottom:0;" type="disc">
        <ul style="margin-top:0;margin-bottom:0;" type="circle">
            <ul style="margin-top:0;margin-bottom:0;" type="square">
                <li style="font-size:12pt;font-family:Tahoma,sans-serif;margin:0;">
                    <span style="font-size:10pt;">ยืนยันที่ตั้งร้านค้าโดย PBH</span>
                </li>
            </ul>
        </ul>
    </ul>

    <p style="margin-right:0;margin-bottom:0;margin-left:90pt;">
        <img src="cid:confirm_shop.png" style="width:315pt; height:auto;">
    </p>

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

    # Handle for not want to send email yet - 'active' when `RECIPIENT_EMAIL` were confing but 'inactive' when `RECIPIENT_EMAIL` not config
    try:

        write_body_content_html = body_content_html
        user_email = [email.strip() for email in get_secret_value("RECIPIENT_EMAIL").split(",")]
        day = str(today.day)
        month_year = today.strftime("%b'%y")
        date_str = f"{day} {month_year}"
        await send_outlook_graph_api(
            bcc_emails=user_email,
            subject=f"Retailers/S2 with incompliant photos (by AI) & Active Suspicious Retailers/S2 as of {date_str}",
            body_content=write_body_content_html,
            is_html=True,
            attachments=attachments,
            inline_images={
                "correct_angel_photo.png": correct_angel_b64,
                "confirm_shop.png": confirm_shop_b64,
            }
        )
        logger.info(f"Send email to user {user_email}")

    except:
        logger.info("Cannot send email to users")
        raise
