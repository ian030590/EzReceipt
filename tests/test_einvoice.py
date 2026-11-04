import base64
import builtins
from types import SimpleNamespace

import numpy as np
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


def test_utf8_item_bytes_are_tried_before_big5(qr_texts):
    raw_name = "Caféé".encode().decode("latin-1")
    left = qr_texts[0][:77] + f":**********:1:1:1:{raw_name}:1:25:"
    assert parse_qr_texts([left]).fields["items"][0].name == "Caféé"


@pytest.mark.parametrize("flag", [0, 1, 2])
@pytest.mark.parametrize("encoding", ["utf-8", "cp950"])
def test_item_names_decode_bytes_independently_of_flag(qr_texts, flag, encoding):
    name = "保鮮合成商品"
    raw = name.encode(encoding)
    encoded = base64.b64encode(raw).decode() if flag == 2 else raw.decode("latin-1")
    left = qr_texts[0][:77] + f":**********:1:1:{flag}:{encoded}:1:25:"
    result = parse_qr_texts([left])
    assert result.fields["items"][0].name == name
    assert result.warnings == []


@pytest.mark.parametrize("flag", [0, 1, 2])
@pytest.mark.parametrize(
    "name,expected",
    [(" \x00合成\x1f商品\x7f \t", "合成商品"), ("   ", None), ("\x00\x1f\x7f", None)],
)
def test_item_names_strip_controls_and_spaces(qr_texts, flag, name, expected):
    raw = name.encode("utf-8")
    encoded = base64.b64encode(raw).decode() if flag == 2 else raw.decode("latin-1")
    left = qr_texts[0][:77] + f":**********:1:1:{flag}:{encoded}:1:25:"
    result = parse_qr_texts([left])
    assert [item.name for item in result.fields["items"]] == ([expected] if expected else [])
    assert result.warnings == ([] if expected else ["einvoice_items_incomplete"])


@pytest.mark.parametrize("flag", [0, 1, 2])
def test_undecodable_item_bytes_keep_original_text(qr_texts, flag):
    encoded = base64.b64encode(b"\xff").decode() if flag == 2 else "ÿ"
    left = qr_texts[0][:77] + f":**********:1:1:{flag}:{encoded}:1:25:"
    assert parse_qr_texts([left]).fields["items"][0].name == encoded


@pytest.mark.parametrize("big5", [False, True])
def test_actual_detector(qr_texts, big5):
    if big5:
        qr_texts = [qr_texts[0].replace(":2:2:1:", ":2:2:0:"), qr_texts[1]]
    image = render_qrs(qr_texts, big5=big5)
    assert len(detect_qr_strings(image)) == 2
    result = extract(image)
    assert result is not None
    assert [item.name for item in result.fields["items"]] == ["乖乖", "拿鐵"]


@pytest.mark.parametrize("encoding", [0, 1, 2])
def test_zxing_raw_byte_convention(qr_texts, encoding, monkeypatch):
    name = "合成商品"
    encoded_name = base64.b64encode(name.encode()).decode() if encoding == 2 else name
    texts = [qr_texts[0][:77] + f":**********:1:1:{encoding}:{encoded_name}:1:25:"]
    image = render_qrs(texts, big5=encoding == 0)

    def no_opencv():
        pytest.fail("An e-invoice decoded by ZXing must not enter the OpenCV loop")

    monkeypatch.setattr("ezreceipt.extractors.einvoice.cv2.QRCodeDetector", no_opencv)
    decoded = detect_qr_strings(image)
    assert parse_qr_texts(decoded).fields["items"][0].name == name
    if encoding != 2:
        expected = name.encode("big5" if encoding == 0 else "utf-8").decode("latin-1")
        assert expected in decoded[0]


def test_opencv_fallback_when_zxing_import_fails(qr_texts, monkeypatch):
    original = builtins.__import__

    def without_zxing(name, *args, **kwargs):
        if name == "zxingcpp":
            raise ImportError("Synthetic missing optional decoder")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_zxing)
    result = extract(render_qrs(qr_texts))
    assert result.fields["invoice_number"] == "AB11223344"
    assert [item.name for item in result.fields["items"]] == ["乖乖", "拿鐵"]


@pytest.mark.parametrize("results", [[], [SimpleNamespace(text="other QR", bytes=b"other QR")]])
def test_opencv_fallback_without_einvoice_header(qr_texts, monkeypatch, results):
    import zxingcpp

    monkeypatch.setattr(zxingcpp, "read_barcodes", lambda *args, **kwargs: results)
    result = extract(render_qrs(qr_texts))
    assert result.fields["total"] == 340


def test_zxing_downscales_and_accepts_text_only_results(qr_texts, monkeypatch):
    import zxingcpp

    calls = []

    def read(image, *, formats):
        calls.append((image.shape, formats))
        return [SimpleNamespace(text=value) for value in qr_texts]

    monkeypatch.setattr(zxingcpp, "read_barcodes", read)
    assert detect_qr_strings(np.zeros((3000, 1500, 3), np.uint8)) == qr_texts
    assert calls == [((2400, 1200), zxingcpp.BarcodeFormat.QRCode)]
