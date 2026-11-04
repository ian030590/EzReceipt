"""OpenCV QR decoding and Taiwan Ministry of Finance e-invoice fields."""

import base64
import logging
import re

import cv2
import numpy as np

from ..imaging import gray
from ..models import LineItem
from ..normalize import parse_date, parse_money, parse_tax_id
from .base import ExtractionResult

logger = logging.getLogger(__name__)
_HEADER = re.compile(r"^[A-Z]{2}\d{8}\d{7}\d{4}[0-9A-Fa-f]{16}\d{16}[A-Za-z0-9+/=]{24}(?::|$)")


def _recover_big5(text: str) -> str:
    try:
        return text.encode("latin-1").decode("big5")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def parse_qr_texts(texts: list[str]) -> ExtractionResult | None:
    left = next((s for s in texts if _HEADER.match(s)), None)
    if left is None:
        return None
    parts = left[78:].split(":", 4)
    if parse_date(left[10:17]) is None:
        return None
    declared_count = None
    encoding = 1
    try:
        declared_count = int(parts[2])
        encoding = int(parts[3])
    except (IndexError, ValueError):
        pass
    if declared_count is not None and declared_count < 0:
        declared_count = None
    sales, total = int(left[21:29], 16), int(left[29:37], 16)
    item_part = parts[4] if len(parts) > 4 else ""
    right = next((s[2:] for s in texts if s.startswith("**")), "")
    tokens = (item_part + right).split(":")
    while tokens and not tokens[-1]:
        tokens.pop()
    items = []
    for offset in range(0, len(tokens) - 2, 3):
        name, qty_text, price_text = tokens[offset : offset + 3]
        if encoding == 2:
            try:
                raw = base64.b64decode(name, validate=True)
                try:
                    name = raw.decode("utf-8")
                except UnicodeDecodeError:
                    name = raw.decode("big5")
            except (ValueError, UnicodeDecodeError):
                logger.debug("Invalid base64 item name")
        elif encoding == 0 or any("\x80" <= char <= "\x9f" for char in name):
            name = _recover_big5(name)
        quantity, price = parse_money(qty_text), parse_money(price_text)
        if name and quantity is not None and price is not None:
            items.append(LineItem(name, quantity, price))
    incomplete = declared_count is not None and len(items) < declared_count
    return ExtractionResult(
        fields={
            "invoice_number": left[:10],
            "date": parse_date(left[10:17]),
            "merchant_tax_id": parse_tax_id(left[45:53]),
            "buyer_tax_id": parse_tax_id(left[37:45]),
            "subtotal": sales,
            "tax": total - sales,
            "total": total,
            "items": items,
        },
        confidence=0.8 if incomplete else 0.98,
        warnings=["einvoice_items_incomplete"] if incomplete else [],
    )


def detect_qr_strings(image: np.ndarray) -> list[str]:
    image_gray = gray(image)
    detectors = [cv2.QRCodeDetector()]
    if hasattr(cv2, "QRCodeDetectorAruco"):
        detectors.append(cv2.QRCodeDetectorAruco())
    decoded: list[str] = []
    for scale in (1, 1.5, 2):
        variant = (
            image_gray
            if scale == 1
            else cv2.resize(
                image_gray,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_CUBIC,
            )
        )
        if scale != 1:
            variant = cv2.addWeighted(variant, 1.8, cv2.GaussianBlur(variant, (0, 0), 1), -0.8, 0)
        for detector in detectors:
            try:
                found, strings, _, _ = detector.detectAndDecodeMulti(variant)
                if found:
                    for value in strings:
                        if value and value not in decoded:
                            decoded.append(value)
            except (cv2.error, UnicodeDecodeError):
                logger.debug("QR decoder could not read this variant", exc_info=True)
        if any(_HEADER.match(value) for value in decoded):
            break
    return decoded


def extract(image: np.ndarray) -> ExtractionResult | None:
    return parse_qr_texts(detect_qr_strings(image))
