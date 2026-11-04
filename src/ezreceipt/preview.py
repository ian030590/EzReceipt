"""Lazy Tk live preview; all widgets stay on the calling thread."""

import logging
import time
from typing import TYPE_CHECKING

import cv2
import numpy as np

from ._tk import enable_dpi_awareness

if TYPE_CHECKING:
    from .scanner import ReceiptScanner

logger = logging.getLogger(__name__)


def run_preview(scanner: "ReceiptScanner") -> None:
    try:
        import tkinter as tk

        from PIL import Image, ImageTk

        enable_dpi_awareness()
        root = tk.Tk()
    except (ImportError, RuntimeError) as exc:
        logger.warning("Preview unavailable; continuing headless: %s", exc)
        return
    except Exception as exc:
        logger.warning("Tk could not start; continuing headless: %s", exc)
        return
    root.title("EzReceipt 自動收據辨識")
    video = tk.Label(root)
    video.pack(fill="both", expand=True)
    status = tk.Label(root, text="等待收據…", font=("Microsoft JhengHei", 14))
    status.pack(padx=12, pady=8)
    result = tk.Label(root, text="", font=("Microsoft JhengHei", 12))
    result.pack(padx=12, pady=(0, 8))
    tk.Label(root, text="Space 擷取  ·  Q / Esc 離開").pack(pady=(0, 8))
    capture_count = 0
    flash_until = 0.0

    def close(event: object = None) -> None:
        scanner._stop.set()
        root.destroy()

    def force(event: object = None) -> None:
        scanner.trigger.force_capture()

    def tick() -> None:
        nonlocal capture_count, flash_until
        frame, update, message, last, count = scanner.snapshot()
        if count != capture_count:
            capture_count = count
            flash_until = time.monotonic() + 0.35
            root.bell()
        if frame is not None:
            if update is not None and update.quad is not None:
                color = (255, 0, 0) if time.monotonic() < flash_until else (0, 190, 0)
                cv2.polylines(frame, [np.int32(update.quad)], True, color, 3)
            scale = min(960 / frame.shape[1], 720 / frame.shape[0])
            frame = cv2.resize(frame, None, fx=scale, fy=scale)
            photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
            video.configure(image=photo)
            video.image = photo
        status.configure(text=message)
        result.configure(text=last)
        if not scanner.is_running:
            close()
        else:
            root.after(30, tick)

    root.protocol("WM_DELETE_WINDOW", close)
    root.bind("<space>", force)
    root.bind("<Escape>", close)
    root.bind("q", close)
    root.bind("Q", close)
    root.after(30, tick)
    try:
        root.mainloop()
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
