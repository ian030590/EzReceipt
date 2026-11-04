import threading
import time

import cv2
import numpy as np
import pytest

from ezreceipt.camera import FrameSource, list_cameras


class FakeCapture:
    def __init__(self, opened=True):
        self.opened = opened
        self.released = False
        self.settings = []
        self.frames = iter([np.full((20, 30, 3), value, np.uint8) for value in (1, 2, 3)])

    def isOpened(self):
        return self.opened

    def set(self, key, value):
        self.settings.append((key, value))

    def get(self, key):
        return 0

    def read(self):
        try:
            return True, next(self.frames)
        except StopIteration:
            return False, None

    def release(self):
        self.released = True


def test_windows_backend_fallback_latest_frame(monkeypatch):
    calls, captures = [], []

    def open_capture(source, backend):
        calls.append((source, backend))
        capture = FakeCapture(opened=backend == cv2.CAP_DSHOW)
        captures.append(capture)
        return capture

    monkeypatch.setattr("ezreceipt.camera.sys.platform", "win32")
    monkeypatch.setattr("ezreceipt.camera.cv2.VideoCapture", open_capture)
    source = FrameSource(2, width=1280, height=720)
    assert source.read() is None
    with source:
        source._thread.join(timeout=2)
        sample = source.read()
        assert sample is not None and sample[0][0, 0, 0] == 3
        sample[0][:] = 0
        assert source.read()[0][0, 0, 0] == 3
        assert source.finished
    assert calls == [(2, cv2.CAP_MSMF), (2, cv2.CAP_DSHOW)]
    assert all(capture.released for capture in captures)
    assert captures[-1].settings == [
        (cv2.CAP_PROP_FRAME_WIDTH, 1280),
        (cv2.CAP_PROP_FRAME_HEIGHT, 720),
    ]


def test_video_and_camera_listing(monkeypatch):
    calls = []

    def open_capture(source, backend):
        calls.append((source, backend))
        return FakeCapture(opened=isinstance(source, str) or source == 1)

    monkeypatch.setattr("ezreceipt.camera.cv2.VideoCapture", open_capture)
    with FrameSource("synthetic.mp4", backend="dshow") as source:
        source._thread.join(timeout=2)
        assert source.finished and source.read() is not None
    assert calls[0] == ("synthetic.mp4", cv2.CAP_ANY)
    assert list_cameras(3) == [1]
    with pytest.raises(OSError):
        FrameSource(7).start()
    with pytest.raises(ValueError):
        FrameSource(0, backend="unknown")


def test_stop_reader(monkeypatch):
    read_once = threading.Event()
    capture = FakeCapture()

    def read():
        read_once.set()
        time.sleep(0.005)
        return True, np.zeros((10, 10, 3), np.uint8)

    capture.read = read
    monkeypatch.setattr("ezreceipt.camera.cv2.VideoCapture", lambda *args: capture)
    source = FrameSource(0).start()
    assert read_once.wait(2)
    source.stop()
    assert not source._thread.is_alive()
    assert capture.released and source.finished
