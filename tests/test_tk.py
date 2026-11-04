import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import PIL
import pytest

from ezreceipt import _tk, preview
from ezreceipt.templates import annotate


@pytest.mark.parametrize(
    "primary_fails,fallback_fails", [(False, False), (True, False), (True, True)]
)
def test_dpi_awareness_windows_fallback(monkeypatch, primary_fails, fallback_fails):
    primary = Mock(side_effect=OSError("shcore failed") if primary_fails else None)
    fallback = Mock(side_effect=OSError("user32 failed") if fallback_fails else None)
    monkeypatch.setattr(_tk.sys, "platform", "win32")
    monkeypatch.setattr(
        _tk.ctypes,
        "windll",
        SimpleNamespace(
            shcore=SimpleNamespace(SetProcessDpiAwareness=primary),
            user32=SimpleNamespace(SetProcessDPIAware=fallback),
        ),
        raising=False,
    )
    assert _tk.enable_dpi_awareness() is None
    primary.assert_called_once_with(1)
    if primary_fails:
        fallback.assert_called_once_with()
    else:
        fallback.assert_not_called()


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_dpi_awareness_noop_elsewhere(monkeypatch, platform):
    windll = Mock()
    monkeypatch.setattr(_tk.sys, "platform", platform)
    monkeypatch.setattr(_tk.ctypes, "windll", windll, raising=False)
    assert _tk.enable_dpi_awareness() is None
    assert windll.mock_calls == []


def test_dpi_awareness_safe_without_windows_libraries(monkeypatch):
    monkeypatch.setattr(_tk.sys, "platform", "win32")
    monkeypatch.delattr(_tk.ctypes, "windll", raising=False)
    assert _tk.enable_dpi_awareness() is None


@pytest.mark.parametrize("window", ["preview", "annotator", "capture_sample"])
def test_dpi_awareness_precedes_tk_root(monkeypatch, tmp_path, window):
    calls = []

    class NoDisplayError(Exception):
        pass

    def start_tk():
        calls.append("tk")
        raise NoDisplayError("no display")

    monkeypatch.setitem(
        sys.modules,
        "tkinter",
        SimpleNamespace(
            Tk=start_tk,
            TclError=NoDisplayError,
            messagebox=SimpleNamespace(),
            simpledialog=SimpleNamespace(),
            ttk=SimpleNamespace(),
        ),
    )
    image_tk = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "PIL.ImageTk", image_tk)
    monkeypatch.setattr(PIL, "ImageTk", image_tk, raising=False)
    owner = preview if window == "preview" else annotate
    monkeypatch.setattr(owner, "enable_dpi_awareness", lambda: calls.append("dpi"))
    if window == "preview":
        preview.run_preview(SimpleNamespace())
    else:
        with pytest.raises(RuntimeError, match="Cannot start template window: no display"):
            if window == "annotator":
                annotate.run_annotator(np.zeros((10, 10, 3), np.uint8), tmp_path)
            else:
                annotate.capture_sample(0)
    assert calls == ["dpi", "tk"]
