import cv2
import numpy as np

from ezreceipt.trigger import AutoTrigger, TriggerConfig


def scene(present=True, offset=0):
    rng = np.random.default_rng(7)
    image = rng.integers(10, 35, (480, 640, 3), dtype=np.uint8)
    if present:
        cv2.rectangle(image, (160 + offset, 40), (480 + offset, 440), (235, 235, 235), -1)
        for y in range(85, 400, 22):
            cv2.rectangle(image, (190 + offset, y), (440 + offset, y + 3), (25, 25, 25), -1)
    return image


def test_hold_remove_rearm():
    trigger = AutoTrigger()
    captures = []
    frames = [scene(False)] * 3 + [scene()] * 30 + [scene(False)] * 8 + [scene()] * 20
    for index, frame in enumerate(frames):
        update = trigger.update(frame, index * 0.1)
        if update.captured is not None:
            captures.append(update)
    assert len(captures) == 2
    assert captures[0].quad is not None
    assert captures[0].captured.shape == (480, 640, 3)


def test_blur_motion_and_force():
    blurred = cv2.GaussianBlur(scene(), (41, 41), 12)
    trigger = AutoTrigger()
    assert all(trigger.update(blurred, index * 0.1).captured is None for index in range(20))
    trigger = AutoTrigger(TriggerConfig(motion_threshold=0.5))
    for index in range(25):
        assert trigger.update(scene(offset=(index % 2) * 25), index * 0.1).captured is None
    trigger.force_capture()
    assert trigger.update(scene(False), 5).captured is not None


def test_swapped_receipt_rearms_and_selects_best():
    trigger = AutoTrigger(TriggerConfig(stable_frames=3, min_interval_s=0))
    for index in range(5):
        trigger.update(scene(), index)
    assert trigger.state == "COOLDOWN"
    changed = scene(offset=70)
    captures = [trigger.update(changed, index).captured for index in range(6, 12)]
    assert sum(frame is not None for frame in captures) == 1
