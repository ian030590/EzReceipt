import time

import cv2
import numpy as np
import pytest

from ezreceipt.dedupe import Deduplicator
from ezreceipt.models import Receipt
from ezreceipt.scanner import ReceiptScanner

from .test_trigger import scene


class FakeSource:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.index = 0
        self.finished = False

    def start(self):
        return self

    def read(self):
        try:
            frame = next(self.frames)
        except StopIteration:
            self.finished = True
            return None
        self.index += 1
        return frame, self.index * 0.1

    def stop(self):
        self.finished = True


def test_scanner_workers_save_dedupe_and_failed_sink(tmp_path, validate_payload):
    received, statuses = [], []

    class Pipeline:
        warmed = False

        def warmup(self):
            self.warmed = True

        def process(self, image):
            assert self.warmed
            return Receipt("local_ocr", total=35, date="2024-11-04", merchant_name="合成店")

    class GoodSink:
        def send(self, receipt):
            received.append(validate_payload(receipt))

    class BadSink:
        def send(self, receipt):
            raise OSError("fake failure")

    scanner = ReceiptScanner(
        pipeline=Pipeline(),
        sinks=[BadSink(), GoodSink()],
        save_images_dir=tmp_path / "影像",
        on_status=statuses.append,
    )
    frames = [scene()] * 15 + [scene(False)] * 10 + [scene()] * 15
    scanner.frame_source = FakeSource(frames)
    scanner.start()
    deadline = time.monotonic() + 10
    while scanner.is_running and time.monotonic() < deadline:
        time.sleep(0.01)
    scanner.stop()
    assert len(received) == 1
    assert "重複收據，已略過" in statuses
    assert "傳送失敗（已記錄）" in statuses
    files = list((tmp_path / "影像").glob("*.jpg"))
    assert len(files) == 1
    assert cv2.imdecode(np.fromfile(files[0], dtype=np.uint8), cv2.IMREAD_COLOR) is not None
    assert received[0]["image_file"] == str(files[0])


def test_dedupe():
    dedupe = Deduplicator(10)
    receipt = Receipt("local_ocr", invoice_number="AB11223344")
    assert not dedupe.is_duplicate(receipt, 0)
    assert dedupe.is_duplicate(receipt, 1)
    assert not dedupe.is_duplicate(receipt, 12)
    partial = Receipt("local_ocr", date="2024-11-04")
    assert not dedupe.is_duplicate(partial, 13)
    assert not dedupe.is_duplicate(partial, 14)
    assert not Deduplicator(0).is_duplicate(receipt)
    with pytest.raises(ValueError):
        Deduplicator(-1)
