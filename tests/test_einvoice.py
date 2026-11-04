import base64

import pytest

from ezreceipt.extractors.einvoice import detect_qr_strings, extract, parse_qr_texts
from ezreceipt.models import Receipt

from .conftest import render_qrs


def test_parse(qr_texts, validate_payload):
    result = parse_qr_texts(qr_texts)
    assert result.fields["date"] == "2024-11-04"
    assert result.fields["subtotal"] == 324
    assert result.fields["total"] == 340
    assert result.fields["tax"] == 16
    assert result.fields["buyer_tax_id"] is None
    assert result.fields["merchant_tax_id"] == "01234567"
    assert [item.name for item in result.fields["items"]] == ["乖乖", "拿鐵"]
    validate_payload(Receipt("tw_einvoice_qr", **result.fields, confidence=result.confidence))


def test_encodings_and_split(qr_texts):
    left = qr_texts[0][:77] + ":**********:1:1:0:" + "合成商品".encode("big5").decode("latin1")
    assert parse_qr_texts([left, "**:2:10:"]).fields["items"][0].name == "合成商品"
    for encoding in ("utf-8", "big5"):
        name = base64.b64encode("合成商品".encode(encoding)).decode()
        left = qr_texts[0][:77] + f":**********:1:1:2:{name}:1:25:"
        assert parse_qr_texts([left]).fields["items"][0].name == "合成商品"
    left = qr_texts[0][:-3]
    assert parse_qr_texts([left]).warnings == ["einvoice_items_incomplete"]
    assert parse_qr_texts(["random", "**right"]) is None


def test_utf8_names_are_not_reinterpreted_as_big5(qr_texts):
    left = qr_texts[0][:77] + ":**********:1:1:1:Caféé:1:25:"
    assert parse_qr_texts([left]).fields["items"][0].name == "Caféé"


@pytest.mark.parametrize("big5", [False, True])
def test_actual_detector(qr_texts, big5):
    if big5:
        qr_texts = [qr_texts[0].replace(":2:2:1:", ":2:2:0:"), qr_texts[1]]
    image = render_qrs(qr_texts, big5=big5)
    assert len(detect_qr_strings(image)) == 2
    result = extract(image)
    assert result is not None
    assert [item.name for item in result.fields["items"]] == ["乖乖", "拿鐵"]
