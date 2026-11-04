import threading
from datetime import date
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from ezreceipt.extractors.einvoice import parse_qr_texts
from ezreceipt.extractors.ocr import OcrLine, parse_generic
from ezreceipt.imaging import preprocess_ocr
from ezreceipt.models import Receipt
from ezreceipt.normalize import parse_date, parse_money
from ezreceipt.pipeline import Pipeline
from ezreceipt.scanner import ReceiptScanner
from ezreceipt.trigger import AutoTrigger

from .test_scanner import FakeSource
from .test_trigger import scene


@pytest.mark.parametrize(
    "text,expected",
    [
        ("萬仟伍佰捌拾元", 580),
        ("— 萬 — 仟 玖 百 陸 拾 元", 960),
        ("✗萬 ✗仟 伍 佰 捌 拾 ✗元", 580),
        ("合计新台带萬仟伍佰捌拾元", 580),
        ("玖百降拾×元", None),
        ("萬仟佰拾元", None),
        ("十五", 15),
        ("萬文仟佰拾元角", None),
        ("一千二百五十", 1250),
    ],
)
def test_grid_money(text, expected):
    assert parse_money(text) == expected


@pytest.mark.parametrize("text", ["1.5.6", "24/11/04", "59/01/01", "1989-01-01"])
def test_date_lower_bounds(text):
    assert parse_date(text) is None


def test_postmark_and_gregorian_bounds():
    assert parse_date("104.05.0613") == "2015-05-06"
    assert parse_date("60年1月1日") == "1971-01-01"
    assert parse_date(f"{date.today().year + 1}/1/1") is not None
    assert parse_date(f"{date.today().year + 2}/1/1") is None


@pytest.mark.parametrize(
    "tail,warnings",
    [
        ("", []),
        (":", []),
        (":**********", []),
        (":**********:0:0:1", []),
        (":**********:1:1:1", ["einvoice_items_incomplete"]),
        (":**********:0:unknown:1", []),
    ],
)
def test_qr_header_without_items(qr_texts, tail, warnings):
    result = parse_qr_texts([qr_texts[0][:77] + tail])
    assert result.fields["invoice_number"] == "AB11223344"
    assert result.fields["total"] == 340 and result.fields["date"] == "2024-11-04"
    assert result.fields["items"] == [] and result.warnings == warnings


def test_finger_still_triggers():
    image = scene()
    cv2.rectangle(image, (150, 180), (225, 260), (20, 20, 20), -1)
    trigger = AutoTrigger()
    captures = [trigger.update(image, i * 0.1).captured for i in range(20)]
    assert sum(capture is not None for capture in captures) == 1


def test_ocr_does_not_crop_small_bright_box():
    image = np.full((1200, 800, 3), 150, np.uint8)
    cv2.rectangle(image, (100, 100), (300, 350), (255, 255, 255), -1)
    cv2.putText(image, "TOTAL 500", (50, 1050), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3)
    assert np.array_equal(preprocess_ocr(image), image)


def test_generic_keeps_first_invoice_and_skips_title():
    lines = [
        OcrLine(t, (0.1, y, 0.9, y + 0.03))
        for t, y in [
            ("免用統一發票收據", 0.01),
            ("合成商店", 0.08),
            ("AB11223344", 0.25),
            ("CD99887766", 0.3),
        ]
    ]
    fields = parse_generic(lines).fields
    assert fields["invoice_number"] == "AB11223344"
    assert fields["merchant_name"] == "合成商店"


@pytest.mark.parametrize(
    "total,warning", [(None, "total_from_items"), (100, "items_total_mismatch")]
)
def test_generic_item_validation(total, warning):
    texts = [("合成商店", 0.05), ("商品 1 x 35 35", 0.4)]
    if total is not None:
        texts.append((f"總計 {total}", 0.8))
    lines = [OcrLine(t, (0.1, y, 0.9, y + 0.03)) for t, y in texts]
    result = Pipeline("ocr", SimpleNamespace(recognize=lambda image: lines)).process(scene())
    assert result.total == (35 if total is None else total)
    assert warning in result.warnings and "total_missing" not in result.warnings


def test_full_queue_drops_capture_without_blocking(caplog):
    scanner = ReceiptScanner(source=FakeSource([scene()] * 5))
    scanner.trigger = SimpleNamespace(
        update=lambda frame, now: SimpleNamespace(captured=frame, message="capture")
    )
    thread = threading.Thread(target=scanner._capture_loop, daemon=True)
    thread.start()
    thread.join(3)
    assert not thread.is_alive() and scanner._queue.qsize() == 2
    assert scanner.snapshot()[2] == "辨識中，請稍候…"
    assert "略過此次擷取" in caplog.text


def test_scanner_missing_total():
    scanner = ReceiptScanner(pipeline=SimpleNamespace(process=lambda _: Receipt("local_ocr")))
    scanner._process_capture(scene())
    assert scanner.snapshot()[3].endswith(" —")


def test_schema_allows_additions(payload_validator):
    from ezreceipt.models import CustomField, LineItem

    payload = Receipt(
        "local_ocr", items=[LineItem("合成品項")], custom_fields=[CustomField("test", "測試", None)]
    ).to_payload()
    for obj in (payload, payload["items"][0], payload["custom_fields"][0]):
        obj["future"] = {"new": True}
    payload_validator.validate(payload)
    assert Receipt.from_payload(payload).items[0].name == "合成品項"


def test_windows_reconfigures_both_streams(monkeypatch):
    from ezreceipt.cli import main

    outputs = []
    monkeypatch.setattr("ezreceipt.cli.sys.platform", "win32")
    monkeypatch.setattr(
        "ezreceipt.cli.sys.stdout",
        SimpleNamespace(reconfigure=lambda **kw: outputs.append(("out", kw))),
    )
    monkeypatch.setattr(
        "ezreceipt.cli.sys.stderr",
        SimpleNamespace(reconfigure=lambda **kw: outputs.append(("err", kw))),
    )
    monkeypatch.setattr("ezreceipt.cli.list_cameras", lambda: [])
    assert main(["cameras"]) == 0
    assert outputs == [("out", {"encoding": "utf-8"}), ("err", {"encoding": "utf-8"})]
