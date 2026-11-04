from types import SimpleNamespace

import numpy as np
import pytest

from ezreceipt.extractors.base import ExtractionResult
from ezreceipt.models import LineItem
from ezreceipt.pipeline import Pipeline

from .test_ocr import line


def test_auto_merge(monkeypatch, validate_payload):
    order = []

    def qr(image):
        order.append("qr")
        return ExtractionResult(
            {"total": 340, "date": "2024-11-04", "items": [LineItem("QR品項", amount=340)]}, 0.98
        )

    def recognize(image):
        order.append("ocr")
        return [line("合成店", 0.05), line("12:30", 0.22), line("總計 999", 0.8)]

    monkeypatch.setattr("ezreceipt.pipeline.einvoice.extract", qr)
    result = Pipeline(ocr_engine=SimpleNamespace(recognize=recognize)).process(
        np.full((1200, 800, 3), 255, np.uint8)
    )
    assert order == ["qr", "ocr"]
    assert result.total == 340 and result.merchant_name == "合成店"
    assert result.time == "12:30:00" and result.source == "tw_einvoice_qr"
    assert result.items[0].name == "QR品項"
    validate_payload(result)


def test_modes_empty_hook_and_incomplete(monkeypatch, validate_payload):
    image = np.full((1200, 800, 3), 255, np.uint8)
    monkeypatch.setattr("ezreceipt.pipeline.einvoice.extract", lambda image: None)
    engine = SimpleNamespace(recognize=lambda image: [line("just a title", 0.05)])
    assert Pipeline(ocr_engine=engine).process(image) is None
    assert Pipeline("einvoice", engine).process(image) is None
    hook = SimpleNamespace(
        extract=lambda lines, image: ExtractionResult(
            {"date": "2024-11-04", "items": [LineItem("A", amount=10), LineItem("B", amount=20)]}
        )
    )
    result = Pipeline("ocr", engine, hook).process(image)
    assert result.source == "local_ocr" and result.date == "2024-11-04"
    assert result.total == 30 and result.warnings == ["total_from_items"]
    validate_payload(result)
    monkeypatch.setattr(
        "ezreceipt.pipeline.einvoice.extract",
        lambda image: ExtractionResult(
            {"total": 30, "items": [LineItem("partial", amount=10)]},
            0.8,
            ["einvoice_items_incomplete"],
        ),
    )
    assert len(Pipeline(ocr_engine=engine, templates=hook).process(image).items) == 2
    with pytest.raises(ValueError):
        Pipeline("cloud")
