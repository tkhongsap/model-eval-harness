"""Tests for ReceiptExtraction / InvoiceLineItem validators — edge-payload behavior."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tasks.ocr_tax_invoice_pipeline.schema.model_response import InvoiceLineItem, ReceiptExtraction


def _minimal_line_item(**overrides) -> dict:
    payload = {
        "INVOICE_NUMBER": None,
        "INVOICE_AMOUNT_BEFORE_VAT": None,
        "INVOICE_VAT_AMOUNT": None,
        "INVOICE_AMOUNT_AFTER_VAT": None,
    }
    payload.update(overrides)
    return payload


def _valid_receipt_payload(**overrides) -> dict:
    payload = {
        "DOC_NAME": "ใบกำกับภาษี",
        "DOC_TYPE": "TaxInvoice",
        "TAX_INVOICE_NUMBER": "INV-001",
        "TAX_INVOICE_DATE": "2026-05-01",
        "VENDOR_TAX_ID": "1234567890123",
        "CUSTOMER_TAX_ID": "3210987654321",
        "BEFORE_VAT_AMOUNT": Decimal("100.00"),
        "VAT_AMOUNT": Decimal("7.00"),
        "AFTER_VAT_AMOUNT": Decimal("107.00"),
        "WITHHOLDING_TAX_AMOUNT": Decimal("3.00"),
        "NET_AMOUNT": Decimal("104.00"),
        "VENDOR_NAME_TH": "ผู้ขาย",
        "VENDOR_ADDRESS_TH": "ที่อยู่ผู้ขาย",
        "CUSTOMER_NAME_TH": "ผู้ซื้อ",
        "CUSTOMER_ADDRESS_TH": "ที่อยู่ผู้ซื้อ",
        "VENDOR_NAME_ENG": "Vendor Co., Ltd.",
        "VENDOR_ADDRESS_ENG": "Vendor Address",
        "CUSTOMER_NAME_ENG": "Customer Co., Ltd.",
        "CUSTOMER_ADDRESS_ENG": "Customer Address",
        "COPY": False,
        "PAYEE_SIGNATURE_FLAG": False,
        "PAYEE_SIGNATURE_NAME": None,
        "AUTHORIZED_RECEIVER_SIGNATURE_FLAG": False,
        "AUTHORIZED_RECEIVER_SIGNATURE_NAME": None,
        "AUTHORIZED_SIGNATORY_SIGNATURE_FLAG": False,
        "AUTHORIZED_SIGNATORY_SIGNATURE_NAME": None,
        "STAMP": False,
    }
    payload.update(overrides)
    return payload


class TestBackfillRequired:
    def test_missing_required_keys_backfill_to_none_and_validate(self):
        # Arrange / Act
        receipt = ReceiptExtraction.model_validate({})

        # Assert — every required field degrades to None/False instead of raising.
        assert receipt.DOC_NAME is None
        assert receipt.TAX_INVOICE_NUMBER is None
        assert receipt.VENDOR_TAX_ID is None
        assert receipt.BEFORE_VAT_AMOUNT is None
        assert receipt.COPY is False
        assert receipt.PAYEE_SIGNATURE_FLAG is False
        assert receipt.line_items == []
        assert receipt.DOC_TYPE == "Other"

    def test_present_required_keys_are_not_overwritten(self):
        # Arrange
        payload = _valid_receipt_payload()

        # Act
        receipt = ReceiptExtraction.model_validate(payload)

        # Assert
        assert receipt.DOC_NAME == "ใบกำกับภาษี"
        assert receipt.VENDOR_TAX_ID == "1234567890123"


class TestBlankDateToNone:
    def test_zero_date_sentinel_with_dash_maps_to_none(self):
        # Arrange
        payload = _valid_receipt_payload(TAX_INVOICE_DATE="0000-01-01")

        # Act
        receipt = ReceiptExtraction.model_validate(payload)

        # Assert
        assert receipt.TAX_INVOICE_DATE is None

    def test_zero_date_sentinel_with_slash_maps_to_none(self):
        # Arrange
        payload = _valid_receipt_payload(TAX_INVOICE_DATE="0000/01/01")

        # Act
        receipt = ReceiptExtraction.model_validate(payload)

        # Assert
        assert receipt.TAX_INVOICE_DATE is None

    def test_blank_string_maps_to_none(self):
        # Arrange
        payload = _valid_receipt_payload(TAX_INVOICE_DATE="   ")

        # Act
        receipt = ReceiptExtraction.model_validate(payload)

        # Assert
        assert receipt.TAX_INVOICE_DATE is None

    def test_none_date_stays_none(self):
        # Arrange
        payload = _valid_receipt_payload(TAX_INVOICE_DATE=None)

        # Act
        receipt = ReceiptExtraction.model_validate(payload)

        # Assert
        assert receipt.TAX_INVOICE_DATE is None

    def test_valid_date_string_parses_normally(self):
        # Arrange
        payload = _valid_receipt_payload(TAX_INVOICE_DATE="2026-05-01")

        # Act
        receipt = ReceiptExtraction.model_validate(payload)

        # Assert
        assert date(2026, 5, 1) == receipt.TAX_INVOICE_DATE


class TestCoerceDocType:
    def test_known_literal_values_pass_through_unchanged(self):
        for value in ("TaxInvoice", "Receipt", "Suspicious", "Other"):
            payload = _valid_receipt_payload(DOC_TYPE=value)
            receipt = ReceiptExtraction.model_validate(payload)
            assert value == receipt.DOC_TYPE

    def test_unrecognised_value_falls_back_to_other(self):
        # Arrange
        payload = _valid_receipt_payload(DOC_TYPE="SomethingElse")

        # Act
        receipt = ReceiptExtraction.model_validate(payload)

        # Assert
        assert receipt.DOC_TYPE == "Other"

    def test_none_value_falls_back_to_other(self):
        # Arrange
        payload = _valid_receipt_payload(DOC_TYPE=None)

        # Act
        receipt = ReceiptExtraction.model_validate(payload)

        # Assert
        assert receipt.DOC_TYPE == "Other"


class TestQuantizeAmounts:
    def test_degenerate_precision_decimal_is_quantized_to_two_dp(self):
        # Arrange — decoder digit-degeneration guard: a runaway-precision Decimal.
        payload = _valid_receipt_payload(BEFORE_VAT_AMOUNT=Decimal("32000.000000000000004"))

        # Act
        receipt = ReceiptExtraction.model_validate(payload)

        # Assert
        assert Decimal("32000.00") == receipt.BEFORE_VAT_AMOUNT

    def test_half_up_rounding_at_the_third_decimal(self):
        # Arrange
        payload = _valid_receipt_payload(VAT_AMOUNT=Decimal("7.005"))

        # Act
        receipt = ReceiptExtraction.model_validate(payload)

        # Assert
        assert Decimal("7.01") == receipt.VAT_AMOUNT

    def test_none_amount_stays_none(self):
        # Arrange
        payload = _valid_receipt_payload(NET_AMOUNT=None)

        # Act
        receipt = ReceiptExtraction.model_validate(payload)

        # Assert
        assert receipt.NET_AMOUNT is None


class TestNoneFlagToFalse:
    def test_explicit_none_flag_becomes_false(self):
        # Arrange
        payload = _valid_receipt_payload(COPY=None, STAMP=None)

        # Act
        receipt = ReceiptExtraction.model_validate(payload)

        # Assert
        assert receipt.COPY is False
        assert receipt.STAMP is False

    def test_explicit_true_flag_is_preserved(self):
        # Arrange
        payload = _valid_receipt_payload(COPY=True, PAYEE_SIGNATURE_FLAG=True)

        # Act
        receipt = ReceiptExtraction.model_validate(payload)

        # Assert
        assert receipt.COPY is True
        assert receipt.PAYEE_SIGNATURE_FLAG is True


class TestNoneItemsToEmpty:
    def test_none_line_items_becomes_empty_list(self):
        # Arrange
        payload = _valid_receipt_payload(line_items=None)

        # Act
        receipt = ReceiptExtraction.model_validate(payload)

        # Assert
        assert receipt.line_items == []

    def test_populated_line_items_are_preserved(self):
        # Arrange
        payload = _valid_receipt_payload(line_items=[_minimal_line_item(INVOICE_NUMBER="34-TT-02-0001")])

        # Act
        receipt = ReceiptExtraction.model_validate(payload)

        # Assert
        assert len(receipt.line_items) == 1
        assert receipt.line_items[0].INVOICE_NUMBER == "34-TT-02-0001"


class TestInvoiceLineItemRounding:
    def test_quantity_and_unit_price_round_to_two_dp(self):
        # Arrange
        payload = _minimal_line_item(QUANTITY=3.14159265, UNIT_PRICE=9.999)

        # Act
        item = InvoiceLineItem.model_validate(payload)

        # Assert
        assert item.QUANTITY == 3.14
        assert item.UNIT_PRICE == 10.0

    def test_none_quantity_and_unit_price_stay_none(self):
        # Arrange
        payload = _minimal_line_item(QUANTITY=None, UNIT_PRICE=None)

        # Act
        item = InvoiceLineItem.model_validate(payload)

        # Assert
        assert item.QUANTITY is None
        assert item.UNIT_PRICE is None

    def test_line_money_fields_are_quantized_to_two_dp(self):
        # Arrange
        payload = _minimal_line_item(
            INVOICE_AMOUNT_BEFORE_VAT=Decimal("1000.000000000004"),
            INVOICE_VAT_AMOUNT=Decimal("70.005"),
            INVOICE_AMOUNT_AFTER_VAT=Decimal("1070.00"),
        )

        # Act
        item = InvoiceLineItem.model_validate(payload)

        # Assert
        assert Decimal("1000.00") == item.INVOICE_AMOUNT_BEFORE_VAT
        assert Decimal("70.01") == item.INVOICE_VAT_AMOUNT
        assert Decimal("1070.00") == item.INVOICE_AMOUNT_AFTER_VAT

    def test_none_line_money_fields_stay_none(self):
        # Arrange
        payload = _minimal_line_item()

        # Act
        item = InvoiceLineItem.model_validate(payload)

        # Assert
        assert item.INVOICE_AMOUNT_BEFORE_VAT is None
        assert item.INVOICE_VAT_AMOUNT is None
        assert item.INVOICE_AMOUNT_AFTER_VAT is None


class TestThaiTextSanitization:
    def test_pua_glyphs_sanitized_across_receipt_fields(self):
        # Arrange: the real 2026-07-08 Scenario-7 leak — PUA tone marks + decomposed sara-am.
        receipt = ReceiptExtraction.model_validate(
            _valid_receipt_payload(
                DOC_NAME="ต\uf70bนฉบับใบเสร็จรับเงิน",
                VENDOR_NAME_TH="บริษัท แอดวานซ\uf70e ไวร\uf70eเลส เน็ทเวอร\uf70eค จำกัด",
                CUSTOMER_NAME_TH="สํานักงานใหญ\uf70a",
            )
        )

        # Assert
        assert receipt.DOC_NAME == "ต้นฉบับใบเสร็จรับเงิน"
        assert receipt.VENDOR_NAME_TH == "บริษัท แอดวานซ์ ไวร์เลส เน็ทเวอร์ค จำกัด"
        assert receipt.CUSTOMER_NAME_TH == "สำนักงานใหญ่"

    def test_line_item_description_newlines_collapse_to_space(self):
        item = InvoiceLineItem.model_validate(
            _minimal_line_item(DESCRIPTION="Revenue Sharing Dec' 25/Postpaid\nRevenue Sharing Dec' 25/Prepaid")
        )

        assert item.DESCRIPTION == "Revenue Sharing Dec' 25/Postpaid Revenue Sharing Dec' 25/Prepaid"

    def test_sanitizer_leaves_json_schema_untouched(self):
        # The validator is runtime-only: the Gemini responseSchema contract must not change.
        schema = ReceiptExtraction.model_json_schema()

        assert len(schema["properties"]) == 34
        assert len(schema["required"]) == 26
