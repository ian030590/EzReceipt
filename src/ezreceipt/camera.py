"""Latest-frame camera/video source with a background reader."""

import logging
import sys
import threading
import time
from types import TracebackType

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class FrameSource:
    def __init__(
        self,
        source: int | str,
        width: int = 1920,
        height: int = 1080,
        backend: str = "auto",
    ) -> None:
        if backend not in ("auto", "msmf", "dshow"):
            raise ValueError("backend must be auto, msmf or dshow")
        self.source, self.width, self.height, self.backend = source, width, height, backend
        self._capture: cv2.VideoCapture | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: tuple[np.ndarray, float] | None = None
        self.finished = False

    def start(self) -> "FrameSource":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self.finished = False
        self._latest = None
        backends = [cv2.CAP_ANY]
        if isinstance(self.source, int):
            if self.backend == "auto" and sys.platform == "win32":
                backends = [cv2.CAP_MSMF, cv2.CAP_DSHOW]
            elif self.backend != "auto":
                backends = [{"msmf": cv2.CAP_MSMF, "dshow": cv2.CAP_DSHOW}[self.backend]]
        for backend in backends:
            capture = cv2.VideoCapture(self.source, backend)
            if capture.isOpened():
                self._capture = capture
                break
            capture.release()
        else:
            raise OSError(f"Cannot open frame source: {self.source}")
        if isinstance(self.source, int):
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._thread = threading.Thread(target=self._reader, name="ezreceipt-camera", daemon=True)
        self._thread.start()
        return self

    def _reader(self) -> None:
        capture = self._capture
        try:
            fps = capture.get(cv2.CAP_PROP_FPS)
            period = 1 / fps if fps > 0 and isinstance(self.source, str) else 0
            while not self._stop.is_set():
                started = time.monotonic()
                ok, frame = capture.read()
                if not ok:
                    break
                with self._lock:
                    self._latest = (frame, started)
                if period:
                    self._stop.wait(max(0, period - (time.monotonic() - started)))
        except Exception:
            logger.exception("Frame reader failed")
        finally:
            capture.release()
            self.finished = True

    def read(self) -> tuple[np.ndarray, float] | None:
        with self._lock:
            return (self._latest[0].copy(), self._latest[1]) if self._latest else None

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                logger.warning("Camera reader is still waiting for the driver")

    def __enter__(self) -> "FrameSource":
        return self.start()

    def __exit__(self, *args: type[BaseException] | BaseException | TracebackType | None) -> None:
        self.stop()


def list_cameras(max_index: int = 5) -> list[int]:
    indices = []
    for index in range(max_index):
        try:
            with FrameSource(index):
                indices.append(index)
        except OSError:
            continue
    return indices
