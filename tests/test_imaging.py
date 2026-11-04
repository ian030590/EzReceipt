import cv2
import numpy as np
import pytest

from ezreceipt.imaging import deskew, detect_document_quad, preprocess_ocr, sharpness

from .test_trigger import scene


@pytest.mark.parametrize("long_side", [800, 959, 960, 961, 1182, 1200, 2400, 2600])
@pytest.mark.parametrize("portrait", [False, True])
def test_ocr_resize_threshold(long_side, portrait):
    shape = (long_side, 400, 3) if portrait else (400, long_side, 3)
    image = np.full(shape, 255, np.uint8)
    result = preprocess_ocr(image)
    expected = 1200 if 1200 / long_side >= 1.25 else min(long_side, 2400)
    assert max(result.shape[:2]) == expected
    if expected == long_side:
        assert result is image


def test_document_geometry():
    image = scene()
    quad = detect_document_quad(image)
    assert quad is not None
    warped = deskew(image, quad)
    assert 390 <= warped.shape[0] <= 405
    assert 310 <= warped.shape[1] <= 325
    assert max(preprocess_ocr(image).shape[:2]) == 1200
    assert sharpness(image, quad) > sharpness(cv2.GaussianBlur(image, (31, 31), 10), quad)
    blank = np.full((2600, 1500, 3), 255, np.uint8)
    assert max(preprocess_ocr(blank).shape[:2]) == 2400
    assert detect_document_quad(blank) is None
