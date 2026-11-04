import sys
import threading
from types import SimpleNamespace

import numpy as np
import PIL

from ezreceipt.preview import run_preview
from ezreceipt.trigger import TriggerUpdate


def test_tk_unavailable_falls_back(monkeypatch, caplog):
    def fail():
        raise RuntimeError("no display")

    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(Tk=fail))
    monkeypatch.setitem(sys.modules, "PIL.ImageTk", SimpleNamespace())
    run_preview(SimpleNamespace())
    assert "headless" in caplog.text


def test_preview_keys_frame_status_and_bell(monkeypatch):
    labels, forced = [], []

    class Root:
        def __init__(self):
            self.bindings, self.callbacks = {}, []
            self.rings = 0

        def title(self, title):
            self.title_text = title

        def bind(self, key, callback):
            self.bindings[key] = callback

        def protocol(self, name, callback):
            self.close = callback

        def after(self, delay, callback):
            assert delay == 30
            self.callbacks.append(callback)

        def bell(self):
            self.rings += 1

        def destroy(self):
            pass

        def mainloop(self):
            self.bindings["<space>"]()
            self.callbacks.pop(0)()
            self.bindings["q"]()
            self.bindings["<Escape>"]()

    class Label:
        def __init__(self, root, **kwargs):
            self.values = kwargs
            labels.append(self)

        def pack(self, **kwargs):
            pass

        def configure(self, **kwargs):
            self.values.update(kwargs)

    root = Root()
    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(Tk=lambda: root, Label=Label))
    image_tk = SimpleNamespace(PhotoImage=lambda image: image)
    monkeypatch.setitem(sys.modules, "PIL.ImageTk", image_tk)
    monkeypatch.setattr(PIL, "ImageTk", image_tk, raising=False)
    frame = np.zeros((480, 640, 3), np.uint8)
    quad = np.array([[100, 100], [500, 100], [500, 400], [100, 400]])
    update = TriggerUpdate("COOLDOWN", frame, quad, 100, "已擷取，辨識中…")
    scanner = SimpleNamespace(
        snapshot=lambda: (frame.copy(), update, "已擷取，辨識中…", "合成店 35", 1),
        trigger=SimpleNamespace(force_capture=lambda: forced.append(True)),
        is_running=False,
        _stop=threading.Event(),
    )
    run_preview(scanner)
    assert forced == [True] and root.rings == 1 and scanner._stop.is_set()
    assert labels[0].values["image"].size == (960, 720)
    assert labels[1].values["text"] == "已擷取，辨識中…"
    assert labels[2].values["text"] == "合成店 35"
