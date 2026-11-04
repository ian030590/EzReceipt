"""Image sizing, document geometry and OCR preparation."""

import cv2
import numpy as np


def gray(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image


def resize_long_side(image: np.ndarray, size: int) -> np.ndarray:
    ratio = size / max(image.shape[:2])
    return cv2.resize(
        image,
        None,
        fx=ratio,
        fy=ratio,
        interpolation=cv2.INTER_CUBIC if ratio > 1 else cv2.INTER_AREA,
    )


def order_quad(points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    center = points.mean(axis=0)
    points = points[np.argsort(np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0]))]
    return np.roll(points, -int(np.argmin(points.sum(axis=1))), axis=0)


def detect_document_quad(
    image: np.ndarray,
    min_area: float = 0.06,
    max_area: float = 0.97,
    brightness_gap: float = 0,
) -> np.ndarray | None:
    image_gray = gray(image)
    blurred = cv2.GaussianBlur(image_gray, (5, 5), 0)
    _, threshold = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(threshold, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    pixels = image_gray.size
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        area = cv2.contourArea(contour)
        if not min_area <= area / pixels <= max_area:
            continue
        rect = cv2.minAreaRect(contour)
        rect_area = rect[1][0] * rect[1][1]
        if rect_area <= 0 or area / rect_area < 0.7:
            continue
        polygon = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            polygon = np.int32(cv2.boxPoints(rect))
        if brightness_gap:
            mask = np.zeros_like(image_gray)
            cv2.fillConvexPoly(mask, polygon, 255)
            inside = cv2.mean(image_gray, mask)[0]
            outside = cv2.mean(image_gray, cv2.bitwise_not(mask))[0]
            if inside < outside + brightness_gap:
                continue
        return order_quad(polygon)
    return None


def deskew(image: np.ndarray, quad: np.ndarray | None = None) -> np.ndarray:
    quad = detect_document_quad(image) if quad is None else quad
    if quad is None:
        return image
    tl, tr, br, bl = order_quad(quad)
    width = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    height = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    if min(width, height) < 40 or max(width, height) / min(width, height) > 12:
        return image
    target = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    transform = cv2.getPerspectiveTransform(np.float32([tl, tr, br, bl]), target)
    return cv2.warpPerspective(image, transform, (width, height))


def sharpness(image: np.ndarray, quad: np.ndarray | None = None) -> float:
    image_gray = gray(image)
    if quad is not None:
        x, y, width, height = cv2.boundingRect(np.int32(quad))
        image_gray = image_gray[max(0, y) : y + height, max(0, x) : x + width]
    return float(cv2.Laplacian(image_gray, cv2.CV_64F).var()) if image_gray.size else 0.0


def preprocess_ocr(image: np.ndarray) -> np.ndarray:
    quad = detect_document_quad(image, min_area=0.2, brightness_gap=12)
    if quad is not None:
        image = deskew(image, quad)
    long_side = max(image.shape[:2])
    if long_side <= 960:
        return resize_long_side(image, 1200)
    if long_side > 2400:
        return resize_long_side(image, 2400)
    return image
