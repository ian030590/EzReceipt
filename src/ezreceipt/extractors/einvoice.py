"""ZXing/OpenCV QR decoding and Taiwan Ministry of Finance e-invoice fields."""

import base64
import logging
import re

import cv2
import numpy as np

from ..imaging import gray, resize_long_side
from ..models import LineItem
from ..normalize import parse_date, parse_money, parse_tax_id
from .base import ExtractionResult

logger = logging.getLogger(__name__)
_HEADER = re.compile(r"^[A-Z]{2}\d{8}\d{7}\d{4}[0-9A-Fa-f]{16}\d{16}[A-Za-z0-9+/=]{24}(?::|$)")


def _decode_item_bytes(raw: bytes, fallback: str) -> str:
    for encoding in ("utf-8", "cp950"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return fallback


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
                name = _decode_item_bytes(raw, name)
            except ValueError:
                logger.debug("Invalid base64 item name")
        elif encoding in (0, 1):
            try:
                raw = name.encode("latin-1")
            except UnicodeEncodeError:
                pass
            else:
                if any(byte >= 128 for byte in raw):
                    name = _decode_item_bytes(raw, name)
        name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
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
    decoded: list[str] = []
    try:
        import zxingcpp
    except ImportError:
        logger.debug("ZXing is unavailable; trying OpenCV")
    else:
        qr_image = (
            resize_long_side(image_gray, 2400) if max(image_gray.shape[:2]) > 2400 else image_gray
        )
        for result in zxingcpp.read_barcodes(qr_image, formats=zxingcpp.BarcodeFormat.QRCode):
            raw = getattr(result, "bytes", None)
            value = raw.decode("latin-1") if raw is not None else result.text
            if value and value not in decoded:
                decoded.append(value)
        if any(_HEADER.match(value) for value in decoded):
            return decoded
    detectors = [cv2.QRCodeDetector()]
    if hasattr(cv2, "QRCodeDetectorAruco"):
        detectors.append(cv2.QRCodeDetectorAruco())
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
