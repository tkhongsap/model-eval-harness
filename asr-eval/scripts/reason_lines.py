"""Customer statements that CARRY the reason label, rather than merely coexisting with it.

THE DEFECT THIS FIXES. `compose_dialogues` drew a `main` reason from a distribution, wrote it
into `business.csv`, and then generated the dialogue without ever consulting it. The label was
assigned, not expressed. Measured consequence: the `ceiling` arm -- a strong labeller reading
the EXACT reference transcript, the upper bound any pipeline could reach -- recovered the
reason on **1 of 137 calls**. It could not have done better. The evidence was not in the call.

A label a perfect reader cannot recover is not ground truth; it is a random number in a CSV,
and every model scored against it measures nothing.

WHAT PRODUCTION'S `reason` ACTUALLY IS. The QA rubric derives it from what the customer says
their problem is. So the fix is not decoration -- it is putting the dialogue back in the
relationship production assumes: the customer states a grievance, and that grievance IS the
reason.

THE TENSION THIS HAS TO HOLD. Making the reason recoverable is easy; making it recoverable
only by READING is the hard part. If each reason got one distinctive sentence, a string match
would score 100% and we would have rebuilt the outcome leak that took a morning to remove --
`leak_probe.py` would catch it, which is why that gate now measures reason and product too.

Three devices keep it honest:

  * **Several distinct phrasings per (scenario, reason)**, sharing no long literal span, so no
    single fragment identifies the reason.
  * **A shared opener pool** (`PROBLEM_SHARED`) drawn alongside them, carrying no reason at
    all, so a matcher that keys on the opening line is right only some of the time.
  * **The reason is stated as circumstance, not as a label.** Nobody says "my reason is
    promotion related"; they say the discount ended and the bill went up. Recovering that
    requires reading the sentence, not finding it.

Slots available: {p} {pq} {self} {amount} {amount_offer} {amount2} {amount3} {months}
{package} {date} {speed} {brand}.
"""

from __future__ import annotations

# Openers that carry NO reason. Drawn alongside the reason-bearing lines so the first
# customer turn is not a reliable index into the label.
PROBLEM_SHARED = [
    "คือ{self}มีเรื่องอยากสอบถามหน่อย{p}",
    "พอดี{self}โทรมาเรื่องบริการที่ใช้อยู่{p}",
    "{self}มีปัญหานิดหน่อย อยากให้ช่วยดูให้หน่อย{p}",
    "คือมันมีเรื่องที่{self}ไม่ค่อยสบายใจ{p}",
    "อยากปรึกษาเรื่องที่ใช้อยู่หน่อย{p}",
    "{self}โทรมาเรื่องที่คุยไว้คราวก่อน{p}",
]

# (scenario, reason) -> customer statements that make that reason readable.
# Every list has at least three phrasings with no shared long span.
PROBLEM_BY_REASON: dict[tuple[str, str], list[str]] = {
    # ---- network ------------------------------------------------------------------
    ("retention", "network"): [
        "สัญญาณที่บ้าน{self}หลุดบ่อยมาก{p} ดูอะไรก็สะดุดตลอด",
        "เน็ตช้าจนใช้งานแทบไม่ได้{p} ตอนเย็นนี่แทบไม่ต้องพูดถึง",
        "โทรออกแล้วเสียงขาด ๆ หาย ๆ ทุกวัน{p} ทนมาหลายเดือนแล้ว",
        "ความเร็วไม่ถึง {speed} อย่างที่บอกไว้สักครั้ง{p}",
    ],
    ("net_slow", "network"): [
        "เน็ตช้ามากตั้งแต่ต้นเดือน{p} โหลดอะไรก็ไม่ขึ้น",
        "ความเร็วตกเหลือไม่ถึงครึ่งของ {speed} {p}",
        "ตอนกลางคืนใช้แทบไม่ได้เลย{p} ประชุมงานก็หลุด",
    ],
    ("coverage_issue", "network"): [
        "แถวบ้าน{self}ไม่มีสัญญาณเลย{p} ต้องออกมาหน้าบ้านถึงจะโทรได้",
        "สัญญาณหายเป็นช่วง ๆ ทั้งวัน{p} เพื่อนบ้านก็เป็นเหมือนกัน",
        "ตั้งแต่ย้ายมาที่นี่ สัญญาณแย่ลงมาก{p}",
    ],
    ("mnp", "network"): [
        "สัญญาณไม่ดีมานาน{self}เลยจะย้ายไปค่ายอื่น{p}",
        "ที่อื่นเขาสัญญาณดีกว่า{p} {self}เลยตัดสินใจย้าย",
        "ใช้มานานแล้วสัญญาณยังไม่ดีขึ้น{p} ขอย้ายค่ายเลย",
    ],
    # ---- save cost ----------------------------------------------------------------
    ("retention", "save cost"): [
        "ค่าบริการเดือนละ {amount} มันแพงเกินไปสำหรับ{self}{p}",
        "ช่วงนี้รายจ่ายเยอะ{p} {amount} ต่อเดือนนี่{self}จ่ายไม่ไหวแล้ว",
        "อยากลดค่าใช้จ่ายลง{p} ตอนนี้จ่ายอยู่ {amount} ทุกเดือน",
    ],
    ("downsell", "save cost"): [
        "อยากลดแพ็กเกจลง{p} {amount} ต่อเดือนมันเกินความจำเป็น",
        "{self}ใช้ไม่ถึงที่จ่ายไป{p} อยากได้ถูกกว่านี้",
        "ขอเปลี่ยนเป็นแพ็กที่ถูกลงหน่อยได้ไหม{pq} จ่าย {amount} มันมากไป",
    ],
    ("mnp", "save cost"): [
        "ค่ายอื่นเขาถูกกว่าเยอะ{p} {self}เลยจะย้าย",
        "ที่นี่ {amount} แต่ที่อื่นได้เท่ากันในราคาถูกกว่า{p}",
        "จ่ายแพงกว่าคนอื่นมานานแล้ว{p} ขอย้ายค่าย",
    ],
    ("billing_dispute", "save cost"): [
        "บิลเดือนนี้ {amount2} สูงกว่าปกติมาก{p} {self}รับไม่ไหว",
        "ทำไมเดือนนี้ขึ้นมาเป็น {amount2} {pq} ปกติจ่ายแค่ {amount}",
        "ค่าใช้จ่ายมันพุ่งขึ้นโดยที่{self}ไม่ได้เปลี่ยนอะไรเลย{p}",
    ],
    ("payment_arrange", "save cost"): [
        "เดือนนี้{self}ยังไม่มีเงินจ่าย {amount} {p} ขอผ่อนผันหน่อยได้ไหม",
        "รายได้ช่วงนี้ไม่แน่นอน{p} ขอเลื่อนจ่ายได้ไหม{pq}",
        "ยอด {amount} จ่ายทีเดียวไม่ไหว{p} ขอแบ่งจ่ายได้ไหม",
    ],
    # ---- promotion related --------------------------------------------------------
    ("retention", "promotion related"): [
        "โปรที่{self}ได้ตอนสมัครมันหมดแล้ว{p} ค่าบริการเลยเด้งขึ้น",
        "ส่วนลดหายไปตั้งแต่เดือนที่แล้ว{p} ไม่มีใครแจ้ง{self}เลย",
        "ตอนสมัครบอกว่าได้ราคาพิเศษ พอครบกำหนดก็ขึ้นราคาเลย{p}",
    ],
    ("downsell", "promotion related"): [
        "โปรเดิมหมดแล้ว{p} อยากดูว่ามีตัวไหนถูกกว่านี้ไหม",
        "ส่วนลดจบไปแล้ว{self}เลยอยากเปลี่ยนแพ็ก{p}",
        "ราคาที่เคยได้มันหมดโปรแล้ว{p} ขอดูตัวเลือกอื่น",
    ],
    ("mnp", "promotion related"): [
        "ค่ายอื่นเขามีโปรใหม่ให้{p} ที่นี่ไม่มีอะไรให้{self}เลย",
        "โปรหมดแล้วไม่มีข้อเสนออะไรต่อ{p} {self}เลยจะย้าย",
        "ที่อื่นมีโปรดีกว่ามาก{p} ขอย้ายค่าย",
    ],
    ("telesale_offer", "promotion related"): [
        "โปรที่โทรมาเสนอนี่มันต่างจากที่{self}ใช้อยู่ยังไง{pq}",
        "ตอนนี้{self}ก็มีโปรอยู่แล้ว{p} ของใหม่ดีกว่าตรงไหน{pq}",
        "ที่เสนอมาราคาเท่าไหร่{pq} ของเดิม{self}จ่าย {amount}",
    ],
    ("billing_dispute", "promotion related"): [
        "ส่วนลดที่ควรจะได้ไม่เข้าในบิล{p} เลยกลายเป็น {amount2}",
        "โปรที่สมัครไว้ไม่ถูกคิดให้{p} บิลเลยผิด",
        "ตกลงกันว่าจะได้ราคาพิเศษ แต่บิลมาเต็มราคา{p}",
    ],
    # ---- contract end -------------------------------------------------------------
    ("retention", "contract end"): [
        "สัญญา{self}ครบกำหนดเดือนนี้แล้ว{p} เลยจะไม่ต่อ",
        "หมดสัญญาแล้ว{p} {self}อยากดูว่าจะเอายังไงต่อ",
        "ผูกสัญญามาครบ {months} แล้ว{p} ตอนนี้อิสระแล้ว",
    ],
    ("mnp", "contract end"): [
        "สัญญาหมดแล้ว{p} {self}ขอย้ายค่ายเลย",
        "ครบกำหนดสัญญาพอดี{p} เลยจะย้าย",
        "ไม่ติดสัญญาแล้ว{p} ขอดำเนินการย้ายค่าย",
    ],
    ("device_promo", "contract end"): [
        "สัญญาเครื่องเก่า{self}จะหมดแล้ว{p} มีอะไรแนะนำไหม{pq}",
        "ผ่อนเครื่องครบแล้ว{p} อยากดูรุ่นใหม่",
        "หมดสัญญาเครื่องพอดี{p} มีโปรอะไรบ้าง{pq}",
    ],
    # ---- dissatisfied service -----------------------------------------------------
    ("retention", "dissatisfied service"): [
        "โทรมากี่ครั้งก็ไม่มีใครแก้ให้{p} {self}เบื่อแล้ว",
        "แจ้งปัญหาไปตั้งหลายรอบ ไม่มีใครติดต่อกลับเลย{p}",
        "บริการแย่มาก{p} รอสายทีนึงเป็นชั่วโมง",
    ],
    ("billing_dispute", "dissatisfied service"): [
        "โทรมาถามเรื่องบิลสามรอบแล้ว ยังไม่มีใครตอบให้ชัด{p}",
        "แต่ละคนบอกไม่เหมือนกันเลย{p} {self}สับสนมาก",
        "แจ้งเรื่องไปแล้วไม่มีความคืบหน้าอะไรเลย{p}",
    ],
    ("net_slow", "dissatisfied service"): [
        "แจ้งเน็ตช้าไปหลายรอบแล้ว ช่างก็ไม่มา{p}",
        "เปิดเคสไว้ตั้งนานยังไม่มีใครติดต่อกลับ{p}",
        "บอกว่าจะแก้ให้ แต่ผ่านไปเป็นเดือนก็เหมือนเดิม{p}",
    ],
    ("coverage_issue", "dissatisfied service"): [
        "แจ้งเรื่องสัญญาณไปแล้วเงียบไปเลย{p}",
        "ไม่มีใครมาดูให้สักที{p} รอมานานแล้ว",
        "ติดต่อไปหลายครั้งแล้วไม่มีอะไรดีขึ้น{p}",
    ],
    ("sim_replace", "dissatisfied service"): [
        "ไปที่ศูนย์แล้วเขาบอกให้โทรมา โทรมาก็บอกให้ไปศูนย์{p}",
        "เรื่องซิมนี่วนอยู่หลายรอบแล้ว{p} ไม่มีใครจัดการให้",
        "แจ้งไปแล้วแต่ยังใช้ไม่ได้{p}",
    ],
    # ---- post to pre --------------------------------------------------------------
    ("retention", "post to pre"): [
        "{self}อยากเปลี่ยนไปใช้แบบเติมเงินแทน{p} รายเดือนมันผูกมัดเกินไป",
        "ขอย้ายไปเป็นพรีเพดได้ไหม{pq} จะได้คุมค่าใช้จ่ายเอง",
        "ไม่อยากผูกรายเดือนแล้ว{p} ขอเป็นเติมเงิน",
    ],
    ("downsell", "post to pre"): [
        "อยากเปลี่ยนจากรายเดือนเป็นเติมเงิน{p}",
        "ขอลงมาเป็นแบบเติมเงินแทนได้ไหม{pq}",
        "รายเดือนไม่คุ้มสำหรับ{self}แล้ว{p} ขอเป็นพรีเพด",
    ],
    ("mnp", "post to pre"): [
        "จะย้ายค่ายแล้วไปสมัครแบบเติมเงินที่โน่น{p}",
        "ขอย้ายออก{p} ที่ใหม่{self}จะใช้เติมเงิน",
        "ไม่เอารายเดือนแล้ว{p} ย้ายค่ายด้วยเลย",
    ],
    # ---- customer reason ----------------------------------------------------------
    ("mnp", "customer reason"): [
        "{self}จะย้ายไปอยู่ต่างจังหวัด{p} เลยขอย้ายค่าย",
        "ที่ทำงานให้เบอร์ใหม่มา{p} เบอร์นี้เลยไม่ได้ใช้แล้ว",
        "เหตุผลส่วนตัว{p} {self}ขอย้ายค่ายอย่างเดียว",
    ],
    ("sim_replace", "customer reason"): [
        "ซิม{self}หายไปเมื่อวาน{p} ขออายัดด่วน",
        "โทรศัพท์โดนขโมย{p} ต้องอายัดซิมเลย",
        "ซิมชำรุดใช้ไม่ได้แล้ว{p} ขอเปลี่ยนใหม่",
    ],
    ("payment_arrange", "customer reason"): [
        "ช่วงนี้{self}ตกงาน{p} ขอผ่อนผันหน่อย",
        "มีเรื่องฉุกเฉินในครอบครัว{p} เลยยังจ่ายไม่ได้",
        "รอเงินเข้าอยู่{p} ขอเลื่อนไปอีกหน่อยได้ไหม{pq}",
    ],
    # ---- device promotion related --------------------------------------------------
    ("device_promo", "device promotion related"): [
        "อยากได้เครื่องใหม่{p} มีผ่อนกี่เดือนบ้าง{pq}",
        "เห็นโปรเครื่องใหม่{p} ผ่อนเดือนละเท่าไหร่{pq}",
        "เครื่องเก่าเริ่มมีปัญหาแล้ว{p} อยากเปลี่ยน",
    ],
    ("device_promo", "promotion related"): [
        "โปรเครื่องที่โทรมาเสนอนี่รวมค่าบริการด้วยไหม{pq}",
        "ถ้ารับเครื่องแล้วโปรรายเดือนเปลี่ยนไหม{pq}",
        "ที่เสนอมามีส่วนลดค่าบริการด้วยหรือเปล่า{pq}",
    ],
    ("telesale_offer", "device promotion related"): [
        "ที่เสนอมามีเครื่องด้วยไหม{pq}",
        "ถ้าเอาเครื่องด้วยต้องผ่อนเดือนละเท่าไหร่{pq}",
        "สนใจเครื่องอยู่เหมือนกัน{p} มีรุ่นไหนบ้าง{pq}",
    ],
    # ---- sale upsell problem -------------------------------------------------------
    ("telesale_offer", "sale upsell problem"): [
        "ที่โทรมาเสนอนี่{self}ไม่ได้ขออะไรไปนะ{p}",
        "มีคนโทรมาขายแล้วสมัครให้เลยโดยที่{self}ไม่ได้ตกลง{p}",
        "ไม่ได้อยากได้เพิ่ม{p} ที่ใช้อยู่ก็พอแล้ว",
    ],
    ("device_promo", "sale upsell problem"): [
        "ที่เสนอมามันเกินความจำเป็นสำหรับ{self}{p}",
        "ไม่ได้อยากได้แพ็กเพิ่ม{p} แค่ถามเรื่องเครื่องเฉย ๆ",
        "เสนอมาเยอะเกินไป{p} {self}ไม่ได้ต้องการขนาดนั้น",
    ],
    ("retention", "sale upsell problem"): [
        "มีคนโทรมาขายเพิ่มโดยที่{self}ไม่ได้ขอ{p} ไม่พอใจมาก",
        "โดนเพิ่มบริการเข้ามาโดย{self}ไม่รู้เรื่องเลย{p}",
        "สมัครอะไรให้{self}ไม่รู้ตัว{p} เลยจะยกเลิกทั้งหมด",
    ],
    # ---- down sell not success -----------------------------------------------------
    ("downsell", "down sell not success"): [
        "ขอลดแพ็กไปแล้วรอบนึง เขาบอกลดไม่ได้{p}",
        "โทรมาขอเปลี่ยนแพ็กแล้วแต่ไม่มีใครทำให้{p}",
        "ขอลงแพ็กถูกกว่านี้ แต่เขาบอกว่าไม่มีให้{p}",
    ],
    # ---- other ---------------------------------------------------------------------
    ("sim_replace", "other"): [
        "ซิมใช้ไม่ได้ตั้งแต่เมื่อคืน{p} ไม่รู้เป็นอะไร",
        "เปลี่ยนเครื่องใหม่แล้วซิมอ่านไม่ขึ้น{p}",
        "ขอซิมใหม่{p} อันเก่ามันมีปัญหา",
    ],
    ("billing_dispute", "other"): [
        "มีรายการในบิลที่{self}ไม่รู้จัก{p}",
        "บิลมีค่าอะไรไม่รู้เพิ่มมา{p} ช่วยตรวจให้หน่อย",
        "อยากให้ช่วยอธิบายบิลเดือนนี้หน่อย{p}",
    ],
    ("payment_arrange", "other"): [
        "อยากสอบถามเรื่องการชำระเงิน{p}",
        "ขอถามเรื่องกำหนดจ่ายหน่อย{p}",
        "เรื่องยอดค้างชำระ{p} อยากคุยหน่อย",
    ],
    ("net_slow", "other"): [
        "เน็ตมีปัญหา{p} ช่วยตรวจสอบให้หน่อย",
        "ใช้งานแล้วรู้สึกผิดปกติ{p} ไม่แน่ใจว่าเป็นอะไร",
        "อยากให้ช่วยเช็กระบบให้หน่อย{p}",
    ],
    ("coverage_issue", "other"): [
        "อยากสอบถามเรื่องพื้นที่ให้บริการ{p}",
        "แถวนี้ใช้ได้ไหม{pq} {self}กำลังจะย้ายมา",
        "ขอถามเรื่องสัญญาณในพื้นที่หน่อย{p}",
    ],
    ("telesale_offer", "other"): [
        "โทรมาเรื่องอะไร{pq} {self}กำลังยุ่งอยู่",
        "มีอะไรให้ช่วยไหม{pq}",
        "ขอฟังรายละเอียดคร่าว ๆ ก่อน{p}",
    ],
}

# The service each product is, in the customer's words. Stated in the dialogue so `product`
# is readable rather than merely implied by which brand was mentioned.
PRODUCT_PHRASE = {
    "postpaid": ["เบอร์รายเดือน", "แพ็กเกจมือถือรายเดือน", "ซิมรายเดือน"],
    "tol": ["เน็ตบ้าน", "อินเทอร์เน็ตบ้าน", "ไฟเบอร์ที่บ้าน"],
    "tvs": ["กล่องทีวี", "แพ็กเกจทีวี", "ช่องทีวีที่สมัครไว้"],
    "unknown": ["บริการที่ใช้อยู่", "ที่สมัครไว้"],
}


def problem_pool(scenario: str, reason: str) -> list[str]:
    """Customer statements for this (scenario, reason), plus the reason-free shared pool.

    Falls back to the shared pool alone when a pair has no authored lines -- which must
    never happen for a pair the generator can actually draw, and `test_reason_coverage`
    asserts exactly that.
    """
    return PROBLEM_BY_REASON.get((scenario, reason), []) + PROBLEM_SHARED
