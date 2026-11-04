"""Capture and recognition workers with per-sink error isolation."""

import logging
import queue
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import cv2
import numpy as np

from .camera import FrameSource
from .dedupe import Deduplicator
from .models import Receipt
from .pipeline import Pipeline
from .sinks import Sink
from .trigger import AutoTrigger, TriggerConfig, TriggerUpdate

logger = logging.getLogger(__name__)


class ReceiptScanner:
    def __init__(
        self,
        source: int | str | FrameSource = 0,
        pipeline: Pipeline | None = None,
        sinks: Iterable[Sink] = (),
        trigger_config: TriggerConfig | None = None,
        dedupe_window_s: float = 600,
        save_images_dir: str | Path | None = None,
        on_receipt: Callable[[Receipt], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        *,
        width: int = 1920,
        height: int = 1080,
        backend: str = "auto",
    ) -> None:
        self.frame_source = (
            FrameSource(source, width, height, backend)
            if isinstance(source, (int, str))
            else source
        )
        self.pipeline = pipeline if pipeline is not None else Pipeline()
        self.sinks = tuple(sinks)
        self.trigger = AutoTrigger(trigger_config)
        self.dedupe = Deduplicator(dedupe_window_s)
        self.save_images_dir = Path(save_images_dir) if save_images_dir is not None else None
        self.on_receipt, self.on_status = on_receipt, on_status
        self._stop = threading.Event()
        self._capture_done = threading.Event()
        self._lock = threading.Lock()
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=2)
        self._capture_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._frame: np.ndarray | None = None
        self._update: TriggerUpdate | None = None
        self._status = "等待收據…"
        self._last_result = ""
        self._capture_count = 0

    @property
    def is_running(self) -> bool:
        return any(
            thread is not None and thread.is_alive()
            for thread in (self._capture_thread, self._worker_thread)
        )

    def snapshot(self) -> tuple[np.ndarray | None, TriggerUpdate | None, str, str, int]:
        with self._lock:
            return (
                self._frame.copy() if self._frame is not None else None,
                self._update,
                self._status,
                self._last_result,
                self._capture_count,
            )

    def _notify(self, callback: Callable | None, value: object) -> None:
        if callback is not None:
            try:
                callback(value)
            except Exception:
                logger.exception("Scanner callback failed")

    def _set_status(self, message: str) -> None:
        with self._lock:
            changed = self._status != message
            self._status = message
        if changed:
            self._notify(self.on_status, message)

    def start(self) -> "ReceiptScanner":
        if self.is_running:
            return self
        self._stop.clear()
        self._capture_done.clear()
        self.frame_source.start()
        self._worker_thread = threading.Thread(
            target=self._worker,
            name="ezreceipt-ocr",
            daemon=True,
        )
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="ezreceipt-capture",
            daemon=True,
        )
        self._worker_thread.start()
        self._capture_thread.start()
        return self

    def _capture_loop(self) -> None:
        previous_timestamp = None
        try:
            while not self._stop.is_set():
                sample = self.frame_source.read()
                if sample is None or sample[1] == previous_timestamp:
                    if self.frame_source.finished:
                        break
                    self._stop.wait(0.01)
                    continue
                frame, timestamp = sample
                previous_timestamp = timestamp
                update = self.trigger.update(frame, timestamp)
                with self._lock:
                    self._frame, self._update = frame, update
                    if update.captured is not None:
                        self._capture_count += 1
                self._set_status(update.message)
                if update.captured is not None:
                    try:
                        self._queue.put_nowait(update.captured)
                    except queue.Full:
                        logger.warning("辨識佇列已滿，略過此次擷取")
                        self._set_status("辨識中，請稍候…")
        except Exception:
            logger.exception("Capture loop failed")
            self._set_status("無法擷取影像")
        finally:
            self.frame_source.stop()
            self._capture_done.set()

    def _save_image(self, receipt: Receipt, image: np.ndarray) -> None:
        if self.save_images_dir is None:
            return
        self.save_images_dir.mkdir(parents=True, exist_ok=True)
        name = f"{receipt.captured_at:%Y%m%d-%H%M%S}_{receipt.receipt_id[:8]}.jpg"
        path = self.save_images_dir / name
        ok, encoded = cv2.imencode(".jpg", image)
        if not ok:
            raise OSError("Could not encode receipt JPEG")
        encoded.tofile(path)
        receipt.image_file = str(path)

    def _worker(self) -> None:
        try:
            self.pipeline.warmup()
        except Exception:
            logger.exception("OCR warmup failed; QR recognition remains available")
        while True:
            try:
                image = self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._capture_done.is_set():
                    return
                continue
            try:
                if image is None:
                    return
                self._process_capture(image)
            except Exception:
                logger.exception("Recognition failed")
                self._set_status("無法辨識")
            finally:
                self._queue.task_done()

    def _process_capture(self, image: np.ndarray) -> None:
        receipt = self.pipeline.process(image)
        if receipt is None:
            self._set_status("無法辨識")
            return
        if self.dedupe.is_duplicate(receipt):
            self._set_status("重複收據，已略過")
            return
        failed = False
        try:
            self._save_image(receipt, image)
        except Exception:
            logger.exception("Could not save receipt image")
            receipt.warnings.append("image_save_failed")
        for sink in self.sinks:
            try:
                sink.send(receipt)
            except Exception:
                failed = True
                logger.exception("Receipt delivery failed for %s", type(sink).__name__)
        total = receipt.total if receipt.total is not None else "—"
        result = f"{receipt.merchant_name or receipt.source} {total}"
        with self._lock:
            self._last_result = result
        self._set_status("傳送失敗（已記錄）" if failed else f"已送出：{result}")
        self._notify(self.on_receipt, receipt)

    def stop(self) -> None:
        self._stop.set()
        if threading.current_thread() in (self._capture_thread, self._worker_thread):
            return
        for thread in (self._capture_thread, self._worker_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join()
        self.frame_source.stop()

    def run(self, preview: bool = True) -> None:
        self.start()
        try:
            if preview:
                from .preview import run_preview

                run_preview(self)
            while self.is_running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            logger.info("Stopping scanner")
        finally:
            self.stop()
