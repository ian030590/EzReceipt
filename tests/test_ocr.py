from types import SimpleNamespace

import numpy as np
import pytest

from ezreceipt.extractors.ocr import OcrEngine, OcrLine, group_rows, parse_generic
from ezreceipt.models import Receipt


def line(text, y, x=0.05, right=0.9, score=0.95):
    return OcrLine(text, (x, y, right, y + 0.04), score)


def test_rows():
    left = line("品名", 0.3, 0.1, 0.4)
    right = line("35", 0.31, 0.8, 0.9)
    assert group_rows([right, line("next", 0.4), left])[0] == [left, right]


@pytest.mark.parametrize(
    ("merchant", "item", "total"),
    [
        ("合成便利商店", "御飯糰 1 x 35 35", "總計 35"),
        ("合成餐館", "拿鐵 65 TX", "合計 NT$65"),
        ("Synthetic Cafe", "Coffee 2 x 30 60", "Amount Due $60.00"),
    ],
)
def test_generic(merchant, item, total, validate_payload):
    lines = [
        line(merchant, 0.04),
        line("電子發票證明聯", 0.1),
        line("2024/11/04 12:30", 0.22),
        line("統編 01234567", 0.28),
        line("AB-11223344", 0.34),
        line(item, 0.42),
        line("小計 60", 0.55),
        line("Tax 5", 0.61),
        line(total, 0.7),
        line("現金 100", 0.8),
        line("找零 35", 0.86),
    ]
    result = parse_generic(lines)
    assert result.fields["merchant_name"] == merchant
    assert result.fields["date"] == "2024-11-04"
    assert result.fields["time"] == "12:30:00"
    assert result.fields["invoice_number"] == "AB11223344"
    assert len(result.fields["items"]) == 1
    assert result.fields["items"][0].amount in (35, 65, 60)
    assert result.fields["total"] in (35, 65, 60)
    assert result.fields["subtotal"] == 60
    assert result.fields["tax"] == 5
    assert result.confidence == pytest.approx(0.68)
    validate_payload(Receipt("local_ocr", **result.fields, confidence=result.confidence))


def test_total_right_and_chinese_money():
    result = parse_generic(
        [line("合成店", 0.04), line("總計", 0.7, right=0.3), line("壹仟貳佰元整", 0.7, x=0.6)]
    )
    assert result.fields["total"] == 1200
    assert "date_missing" in result.warnings
    assert parse_generic([]).confidence == pytest.approx(0.1)


def test_wrapper():
    result = SimpleNamespace(
        txts=["合成品"], boxes=np.array([[[10, 20], [90, 20], [90, 40], [10, 40]]]), scores=[0.8]
    )
    engine = OcrEngine(engine=lambda image: result)
    assert engine.recognize(np.zeros((100, 100, 3), np.uint8)) == [
        OcrLine("合成品", (0.1, 0.2, 0.9, 0.4), 0.8)
    ]
    assert OcrEngine(engine=lambda image: None).recognize(np.zeros((10, 10, 3))) == []


def test_nearest_amount_and_separate_date_cells():
    result = parse_generic(
        [
            line("合成店", 0.05),
            line("民國 113 年", 0.3, x=0.05, right=0.3),
            line("11 月 4 日", 0.3, x=0.35, right=0.65),
            line("Tax ID 01234567", 0.4),
            line("Total", 0.7, right=0.3),
            line("100", 0.7, x=0.4, right=0.5),
            line("999", 0.7, x=0.8, right=0.9),
        ]
    )
    assert result.fields["total"] == 100
    assert result.fields["date"] == "2024-11-04"
    assert result.fields.get("tax") is None


def test_missing_models_never_download(monkeypatch, tmp_path):
    from ezreceipt.extractors.ocr import _create_engine

    monkeypatch.setattr(
        "ezreceipt.extractors.ocr.find_spec",
        lambda name: SimpleNamespace(origin=str(tmp_path / "rapidocr" / "__init__.py")),
    )
    with pytest.raises(RuntimeError, match="Bundled OCR model missing"):
        _create_engine()
