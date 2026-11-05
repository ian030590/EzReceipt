"""Cropped receipt pages on a pure white background, saved as PDF."""

import io
from pathlib import Path

import cv2
import numpy as np

from .imaging import deskew, detect_document_quad, resize_long_side

# Paper divided by its local background lands near 1.0, and WHITE and above
# become pure white. The mapping stays linear with no black clipping: faint
# thermal print keeps its stroke edges and blurred QR modules stay decodable.
WHITE = 0.96
# Near-white pixels whose surroundings are also near white (paper grain, not
# the soft edge of a faint stroke) are snapped to pure white.
SNAP = 245
# Background estimates never drop below this share of the paper level, so large
# dark areas such as a black header band stay dark instead of turning white.
FLOOR = 0.4
MAX_SIDE = 3000
A4_LONG_INCHES = 11.69


def _whiten(page: np.ndarray) -> np.ndarray:
    small = resize_long_side(page, 600) if max(page.shape[:2]) > 600 else page
    size = max(3, round(max(small.shape[:2]) / 25)) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (size, size))
    background = cv2.morphologyEx(small, cv2.MORPH_CLOSE, kernel).astype(np.float32)
    background = cv2.GaussianBlur(background, (0, 0), size / 2)
    paper = np.percentile(background.reshape(-1, 3), 95, axis=0)
    background = np.maximum(background, np.maximum(paper * FLOOR, 1))
    background = cv2.resize(background, page.shape[1::-1], interpolation=cv2.INTER_LINEAR)
    level = page.astype(np.float32) / background
    page = np.clip(np.round(level * (255 / WHITE)), 0, 255).astype(np.uint8)
    lightness = cv2.GaussianBlur(page.min(axis=2).astype(np.float32), (0, 0), 1.5)
    page[lightness >= SNAP] = 255
    return page


def _clear_edges(page: np.ndarray, band: float = 0.04) -> np.ndarray:
    """Whiten desk slivers and paper-edge lines that touch the page border."""
    height, width = page.shape[:2]
    depth = max(3, round(min(height, width) * band))
    marks = (page.min(axis=2) < 255).astype(np.uint8)
    marks[depth:-depth, depth:-depth] = 0
    _, labels = cv2.connectedComponents(marks)
    ring = np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    edge = np.unique(ring[ring > 0])
    if edge.size:
        page[np.isin(labels, edge)] = 255
    return page


def _crop_to_ink(page: np.ndarray, margin: float = 0.03) -> np.ndarray:
    marks = (page.min(axis=2) < 255).astype(np.float32)
    # Any mark counts, faint print included, unless it is an isolated speck.
    nearby = cv2.boxFilter(marks, -1, (9, 9), normalize=False)
    points = cv2.findNonZero(((nearby >= 6) & (marks > 0)).astype(np.uint8))
    if points is None:
        return page
    x, y, width, height = cv2.boundingRect(points)
    pad = round(max(width, height) * margin)
    return page[max(0, y - pad) : y + height + pad, max(0, x - pad) : x + width + pad]


def clean_page(image: np.ndarray) -> np.ndarray:
    """Crop the receipt out of a BGR photo, straighten it and whiten the paper."""
    if image is None or image.size == 0:
        raise ValueError("image must not be empty")
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    ratio = min(1.0, 1000 / max(image.shape[:2]))
    small = cv2.resize(image, None, fx=ratio, fy=ratio, interpolation=cv2.INTER_AREA)
    # Up to 90% of the frame: a larger bright area is usually a light desk.
    quad = detect_document_quad(small, 0.06, 0.9, 12)
    page = image
    if quad is not None:
        page = deskew(image, quad / ratio)
        inset = max(2, round(min(page.shape[:2]) * 0.01))
        if min(page.shape[:2]) > 4 * inset:
            page = page[inset:-inset, inset:-inset]
    if max(page.shape[:2]) > MAX_SIDE:
        page = resize_long_side(page, MAX_SIDE)
    # On a white page the paper edge is invisible, so trimming to the printed
    # content also repairs a crop that included a light desk.
    return np.ascontiguousarray(_clear_edges(_crop_to_ink(_whiten(page))))


def save_receipt_pdf(image: np.ndarray, path: str | Path) -> Path:
    """Write the cleaned receipt as a one-page PDF and return its path."""
    from PIL import Image

    page = clean_page(image)
    # Like a 200 DPI desktop scan, but never longer than an A4 page.
    dpi = max(200.0, max(page.shape[:2]) / A4_LONG_INCHES)
    buffer = io.BytesIO()
    rgb = Image.fromarray(cv2.cvtColor(page, cv2.COLOR_BGR2RGB))
    # Full-resolution chroma keeps the white around coloured ink exactly white.
    rgb.save(buffer, "PDF", resolution=dpi, quality=90, subsampling=0)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buffer.getvalue())
    return path
