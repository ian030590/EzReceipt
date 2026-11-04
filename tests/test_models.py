from datetime import datetime

import pytest

from ezreceipt.models import CustomField, LineItem, Receipt


def test_roundtrip(validate_payload):
    receipt = Receipt(
        "local_ocr",
        items=[LineItem("合成品項", 2, 17.555)],
        total=35.11,
        custom_fields=[CustomField("serial_no", "交易序號", "SYNTHETIC")],
    )
    payload = validate_payload(receipt)
    assert payload["items"][0]["amount"] == 35.11
    assert isinstance(payload["items"][0]["quantity"], int)
    assert Receipt.from_payload(payload).to_payload() == payload
    assert Receipt("local_ocr").receipt_id != receipt.receipt_id


def test_invalid_models():
    with pytest.raises(ValueError):
        CustomField("Bad Key", "測試", None)
    with pytest.raises(ValueError):
        Receipt("local_ocr", captured_at=datetime(2024, 1, 1))
    with pytest.raises(ValueError):
        Receipt("cloud")
    with pytest.raises(ValueError):
        Receipt("local_ocr", confidence=1.1)
    with pytest.raises(ValueError):
        Receipt("local_ocr", total=float("nan")).to_payload()


def test_numeric_precision_and_utc(validate_payload):
    receipt = Receipt(
        "local_ocr",
        items=[LineItem("按重計價", 0.12345, 100)],
        custom_fields=[CustomField("weight", "重量", 0.12345)],
    )
    payload = validate_payload(receipt)
    assert payload["items"][0]["quantity"] == 0.12345
    assert payload["items"][0]["amount"] == 12.35
    assert payload["custom_fields"][0]["value"] == 0.12345
    payload["captured_at"] = "2024-11-04T12:30:00Z"
    assert Receipt.from_payload(payload).captured_at.utcoffset().total_seconds() == 0
