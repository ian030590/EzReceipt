"""Automatic capture state machine driven only by frames and monotonic time."""

from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

from .imaging import detect_document_quad, gray, resize_long_side, sharpness


@dataclass
class TriggerConfig:
    analysis_width: int = 320
    min_area: float = 0.06
    max_area: float = 0.97
    motion_threshold: float = 4.0
    stable_frames: int = 8
    min_sharpness: float = 40
    absent_frames: int = 5
    rearm_threshold: float = 18
    min_interval_s: float = 1.5

    def __post_init__(self) -> None:
        if min(self.analysis_width, self.stable_frames, self.absent_frames) < 1:
            raise ValueError("Frame counts and analysis width must be positive")
        if not 0 < self.min_area < self.max_area <= 1:
            raise ValueError("Invalid document area bounds")


@dataclass
class TriggerUpdate:
    state: str
    captured: np.ndarray | None
    quad: np.ndarray | None
    sharpness: float
    message: str


_MESSAGES = {
    "WAITING": "等待收據…",
    "STEADYING": "偵測到收據，請保持穩定",
    "COOLDOWN": "請移開收據以擷取下一張",
}


class AutoTrigger:
    def __init__(self, config: TriggerConfig | None = None) -> None:
        self.config = config or TriggerConfig()
        self.state = "WAITING"
        self._previous: np.ndarray | None = None
        self._captured_analysis: np.ndarray | None = None
        self._last_capture = float("-inf")
        self._frames: deque[tuple[float, np.ndarray, np.ndarray]] = deque(
            maxlen=self.config.stable_frames,
        )
        self._absence = self._count = 0
        self._qr_present = False
        self._detector = cv2.QRCodeDetector()
        self._force = False

    def force_capture(self) -> None:
        self._force = True

    def update(self, frame_bgr: np.ndarray, now: float) -> TriggerUpdate:
        config = self.config
        ratio = config.analysis_width / frame_bgr.shape[1]
        analysis = cv2.resize(
            gray(frame_bgr), None, fx=ratio, fy=ratio, interpolation=cv2.INTER_AREA
        )
        quad_small = detect_document_quad(analysis, config.min_area, config.max_area, 12)
        quad = quad_small / ratio if quad_small is not None else None
        if self._count % 3 == 0:
            qr_image = resize_long_side(gray(frame_bgr), 640)
            try:
                self._qr_present = bool(self._detector.detectMulti(qr_image)[0])
            except cv2.error:
                self._qr_present = False
        self._count += 1
        present = quad is not None or self._qr_present
        same_size = self._previous is not None and self._previous.shape == analysis.shape
        motion = float(np.mean(cv2.absdiff(analysis, self._previous))) if same_size else 255
        self._previous = analysis
        focus = sharpness(frame_bgr, quad)
        capture = None
        if self._force:
            self._force = False
            capture = frame_bgr.copy()
        elif self.state == "COOLDOWN":
            self._absence = self._absence + 1 if not present else 0
            changed = False
            if self._captured_analysis is not None:
                changed = self._captured_analysis.shape != analysis.shape
                if not changed:
                    difference = float(np.mean(cv2.absdiff(analysis, self._captured_analysis)))
                    changed = difference > config.rearm_threshold
            if now - self._last_capture >= config.min_interval_s and (
                self._absence >= config.absent_frames or (present and changed)
            ):
                self.state = "STEADYING" if present else "WAITING"
                self._frames.clear()
        elif not present:
            self.state = "WAITING"
            self._frames.clear()
        else:
            self.state = "STEADYING"
            if motion >= config.motion_threshold:
                self._frames.clear()
            else:
                self._frames.append((focus, frame_bgr.copy(), analysis.copy()))
                if len(self._frames) >= config.stable_frames:
                    best_focus, best_frame, best_analysis = max(
                        self._frames,
                        key=lambda pair: pair[0],
                    )
                    if best_focus >= config.min_sharpness:
                        capture = best_frame
                        analysis = best_analysis
        if capture is not None:
            self.state = "COOLDOWN"
            self._last_capture = now
            self._captured_analysis = analysis.copy()
            self._absence = 0
            self._frames.clear()
        message = "已擷取，辨識中…" if capture is not None else _MESSAGES[self.state]
        return TriggerUpdate(self.state, capture, quad, focus, message)
