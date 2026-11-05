import io
import re

import cv2
import numpy as np
import pytest
import zxingcpp
from PIL import Image

from ezreceipt.pdf import clean_page, save_receipt_pdf

from .conftest import render_qrs


def receipt_paper(qr_texts):
    paper = np.full((900, 420, 3), (218, 232, 238), np.uint8)
    cv2.rectangle(paper, (30, 25), (390, 85), (15, 15, 15), -1)
    for y in range(130, 330, 40):
        cv2.rectangle(paper, (40, y), (300, y + 8), (30, 30, 30), -1)
    cv2.rectangle(paper, (40, 560), (330, 563), (165, 165, 165), -1)
    cv2.circle(paper, (300, 760), 70, (40, 30, 200), 6)
    qr = render_qrs(qr_texts[:1])
    paper[350 : 350 + qr.shape[0] // 2, 40 : 40 + qr.shape[1] // 2] = cv2.resize(
        qr, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA
    )
    return paper


def photo(qr_texts, shadow=0.6):
    rng = np.random.default_rng(5)
    desk = cv2.resize(rng.integers(20, 90, (60, 8, 3), dtype=np.uint8), (1280, 960))
    paper = receipt_paper(qr_texts)
    height, width = paper.shape[:2]
    corners = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    target = np.float32([[470, 50], [830, 80], [800, 900], [430, 880]])
    matrix = cv2.getPerspectiveTransform(corners, target)
    size = desk.shape[1::-1]
    warped = cv2.warpPerspective(paper, matrix, size).astype(np.float32)
    mask = cv2.warpPerspective(np.ones((height, width), np.float32), matrix, size)[:, :, None]
    scene = desk * (1 - mask) + warped * mask
    light = 1 - (1 - shadow) * np.clip(np.linspace(-0.5, 1.5, size[0]), 0, 1)
    scene = scene * light[None, :, None] + rng.normal(0, 3, scene.shape)
    return cv2.GaussianBlur(np.clip(scene, 0, 255).astype(np.uint8), (0, 0), 0.8)


def border(page):
    return np.concatenate([page[0], page[-1], page[:, 0], page[:, -1]])


def test_crops_receipt_and_whitens_background(qr_texts):
    page = clean_page(photo(qr_texts))
    height, width = page.shape[:2]
    assert 780 <= height <= 900 and 330 <= width <= 420
    assert (border(page) == 255).all()
    assert (page == 255).all(axis=2).mean() > 0.7
    assert page[: height // 8].min() < 60, "black header band must stay dark"
    red = (page[:, :, 2] > 150) & (page[:, :, 1] < 110) & (page[:, :, 0] < 110)
    assert red.sum() > 500, "coloured ink must keep its colour"
    assert zxingcpp.read_barcodes(page), "the QR code must stay decodable"


def test_keeps_faint_print():
    rng = np.random.default_rng(2)
    paper = np.full((600, 420, 3), (218, 232, 238), np.uint8)
    cv2.rectangle(paper, (30, 30), (390, 60), (20, 20, 20), -1)
    for row in range(5):
        cv2.putText(
            paper,
            "TOTAL 1234 NT",
            (40, 260 + 40 * row),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (185, 190, 195),
            1,
            cv2.LINE_AA,
        )
    corners = np.float32([[0, 0], [420, 0], [420, 600], [0, 600]])
    target = np.float32([[300, 90], [700, 110], [690, 700], [290, 690]])
    matrix = cv2.getPerspectiveTransform(corners, target)
    desk = cv2.resize(rng.integers(20, 90, (60, 8, 3), dtype=np.uint8), (1000, 800))
    mask = cv2.warpPerspective(np.ones((600, 420), np.float32), matrix, (1000, 800))[:, :, None]
    warped = cv2.warpPerspective(paper, matrix, (1000, 800))
    scene = desk * (1 - mask) + warped * mask + rng.normal(0, 3, warped.shape)
    page = clean_page(cv2.GaussianBlur(np.clip(scene, 0, 255).astype(np.uint8), (0, 0), 0.8))
    assert page.shape[0] > 300, "faint lines must not be cropped away"
    faint = page[page.shape[0] // 3 :]
    assert (faint.min(axis=2) < 255).sum() > 4000, "faint strokes must stay whole"
    assert (border(page) == 255).all()


def test_without_paper_edges_crops_to_printed_content():
    image = np.full((800, 1200, 3), 225, np.uint8)
    cv2.rectangle(image, (500, 200), (700, 210), (40, 40, 40), -1)
    cv2.rectangle(image, (500, 500), (650, 512), (40, 40, 40), -1)
    page = clean_page(image)
    assert page.shape[0] < 400 and page.shape[1] < 300
    assert (border(page) == 255).all()
    assert page.min() < 100


@pytest.mark.parametrize("shape", [(1, 1, 3), (5, 5), (30, 20, 3)])
def test_tiny_and_gray_images(shape):
    assert clean_page(np.full(shape, 128, np.uint8)).ndim == 3


def test_empty_image_is_rejected():
    with pytest.raises(ValueError):
        clean_page(np.zeros((0, 0, 3), np.uint8))


def test_pdf_page_is_the_cropped_receipt(tmp_path, qr_texts):
    image = photo(qr_texts)
    path = save_receipt_pdf(image, tmp_path / "收據" / "第一張.pdf")
    data = path.read_bytes()
    assert data.startswith(b"%PDF")
    page = clean_page(image)
    media = [float(value) for value in re.search(rb"MediaBox \[([^\]]*)\]", data)[1].split()]
    assert media[2] == pytest.approx(page.shape[1] * 72 / 200, abs=0.1)
    assert media[3] == pytest.approx(page.shape[0] * 72 / 200, abs=0.1)
    start = data.index(b"stream\n") + len(b"stream\n")
    embedded = np.asarray(Image.open(io.BytesIO(data[start : data.index(b"endstream")])))
    assert embedded.shape[:2] == page.shape[:2]
    ink = cv2.dilate((page.min(axis=2) < 255).astype(np.uint8), np.ones((17, 17), np.uint8))
    assert (embedded[ink == 0] == 255).all(), "blank paper must be exactly white in the PDF"
    assert zxingcpp.read_barcodes(embedded)


def test_large_page_fits_a4(tmp_path):
    image = np.full((5000, 900, 3), 255, np.uint8)
    for y in range(50, 4950, 200):
        cv2.rectangle(image, (60, y), (840, y + 20), (0, 0, 0), -1)
    data = save_receipt_pdf(image, tmp_path / "long.pdf").read_bytes()
    height = float(re.search(rb"MediaBox \[([^\]]*)\]", data)[1].split()[3])
    assert height == pytest.approx(11.69 * 72, abs=1)
