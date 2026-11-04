"""Local OCR, geometric rows and conservative receipt heuristics."""

import logging
import re
import statistics
import threading
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from ..models import LineItem
from ..normalize import (
    normalize_text,
    parse_date,
    parse_money,
    parse_tax_id,
    parse_time,
    to_traditional,
)
from .base import ExtractionResult

_ENGINE: Any = None
_ENGINE_LOCK = threading.Lock()


@dataclass(frozen=True)
class OcrLine:
    text: str
    box: tuple[float, float, float, float]
    score: float = 1.0


class TemplateHook(Protocol):
    """Phase 2 can match/apply templates here, returning None for generic fallback."""

    def extract(self, lines: list[OcrLine], image: np.ndarray) -> ExtractionResult | None: ...


def _create_engine() -> Any:
    spec = find_spec("rapidocr")
    if spec is None or spec.origin is None:
        raise RuntimeError("RapidOCR is not installed")
    model_dir = Path(spec.origin).parent / "models"
    models = {
        "Det": "PP-OCRv6_det_small.onnx",
        "Rec": "PP-OCRv6_rec_small.onnx",
        "Cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    }
    params: dict[str, Any] = {"Global.log_level": "warning"}
    for task, filename in models.items():
        path = model_dir / filename
        if not path.is_file():
            raise RuntimeError(f"Bundled OCR model missing: {filename}; install RapidOCR 3.10+")
        params[f"{task}.model_path"] = str(path)
    from rapidocr import RapidOCR

    logging.getLogger("RapidOCR").setLevel(logging.WARNING)
    return RapidOCR(params=params)


class OcrEngine:
    def __init__(self, engine: Any = None) -> None:
        self._engine = engine

    def recognize(self, img_bgr: np.ndarray) -> list[OcrLine]:
        global _ENGINE
        with _ENGINE_LOCK:
            if self._engine is None and _ENGINE is None:
                _ENGINE = _create_engine()
            engine = self._engine if self._engine is not None else _ENGINE
            result = engine(img_bgr)
        if result is None or getattr(result, "txts", None) is None:
            return []
        texts, boxes, scores = result.txts, result.boxes, result.scores
        if boxes is None or scores is None:
            return []
        height, width = img_bgr.shape[:2]
        lines = []
        for text, box, score in zip(texts, boxes, scores):
            points = np.asarray(box)
            x0, y0 = points.min(axis=0) / [width, height]
            x1, y1 = points.max(axis=0) / [width, height]
            bounds = tuple(float(v) for v in np.clip([x0, y0, x1, y1], 0, 1))
            lines.append(OcrLine(str(text), bounds, float(score)))
        return lines

    def warmup(self) -> None:
        self.recognize(np.full((64, 64, 3), 255, np.uint8))


def group_rows(lines: list[OcrLine]) -> list[list[OcrLine]]:
    rows: list[list[OcrLine]] = []
    for line in sorted(lines, key=lambda line: (sum(line.box[1::2]) / 2, line.box[0])):
        center = (line.box[1] + line.box[3]) / 2
        candidates = [
            row
            for row in rows
            if abs(center - statistics.median((other.box[1] + other.box[3]) / 2 for other in row))
            <= min(
                line.box[3] - line.box[1],
                statistics.median(other.box[3] - other.box[1] for other in row),
            )
            * 0.6
        ]
        row = (
            min(
                candidates,
                key=lambda row: abs(
                    center - statistics.median((other.box[1] + other.box[3]) / 2 for other in row)
                ),
            )
            if candidates
            else None
        )
        if row is None:
            rows.append([line])
        else:
            row.append(line)
    return [sorted(row, key=lambda line: line.box[0]) for row in rows]


_TOTALS = ("總計", "合計", "總金額", "應付", "應收", "實付", "金額合計", "amount due", "total")
_PAYMENTS = ("找零", "找錢", "現金", "收現", "實收", "信用卡", "change", "cash", "credit card")
_NUMBER = r"[-+]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)"


def _label_match(label: str, text: str) -> re.Match | None:
    pattern = rf"\b{re.escape(label)}\b" if label.isascii() else re.escape(label)
    return re.search(pattern, text, re.IGNORECASE)


def _amount_after(text: str, label: str) -> float | None:
    match = _label_match(label, text)
    if match is None:
        return None
    suffix = text[match.end() :].strip(" :：=")
    amount = parse_money(suffix)
    if amount is not None:
        return amount
    numbers = re.findall(_NUMBER, suffix)
    return parse_money(numbers[0]) if numbers else None


def _row_amount(row: list[OcrLine], label: str) -> float | None:
    for index, line in enumerate(row):
        text = normalize_text(line.text)
        if _label_match(label, text) is None:
            continue
        amount = _amount_after(text, label)
        if amount is not None:
            return amount
        for neighbor in row[index + 1 :]:
            amount = parse_money(neighbor.text)
            if amount is not None:
                return amount
    return None


def parse_item_row(row: list[OcrLine]) -> LineItem | None:
    text = " ".join(line.text for line in row).strip()
    text = re.sub(r"\s+(?:TX|T|N)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+[xX×*]\s+", " ", text)
    match = re.fullmatch(rf"(.+?)\s+({_NUMBER}(?:\s+{_NUMBER}){{0,2}})", text)
    if not match or not re.search(r"[^\W\d_]|[\u3400-\u9fff]", match[1]):
        return None
    name = to_traditional(match[1].strip())
    if any(label in normalize_text(name) for label in ("統編", "編號", "電話", "日期", "地址")):
        return None
    values = [parse_money(number) for number in match[2].split()]
    if len(values) == 3:
        return LineItem(name, values[0], values[1], values[2])
    if len(values) == 2:
        return LineItem(name, values[0], None, values[1])
    return LineItem(name, amount=values[0])


def parse_generic(lines: list[OcrLine]) -> ExtractionResult:
    rows = group_rows(lines)
    row_texts = [normalize_text(" ".join(line.text for line in row)) for row in rows]
    fields: dict[str, Any] = {}
    for line in sorted(lines, key=lambda line: (line.box[1], line.box[0])):
        text = normalize_text(line.text)
        if "date" not in fields and (value := parse_date(text)):
            fields["date"] = value
        if "time" not in fields and (value := parse_time(text)):
            fields["time"] = value
        invoice = re.search(r"(?<![A-Z0-9])([A-Z]{2})-?(\d{8})(?!\d)", text.upper())
        if invoice and "invoice_number" not in fields:
            fields["invoice_number"] = "".join(invoice.groups())
        if line.box[1] <= 0.2 and "merchant_name" not in fields:
            title = re.search(r"(?:收據|發票|證明單|證明聯)$|^(?:receipt|invoice)$", text, re.I)
            if (
                not title
                and not parse_date(text)
                and not parse_time(text)
                and not invoice
                and not re.search(r"統一編號|統編|賣方|電話|tel|tax", text, re.I)
                and re.search(r"[\u3400-\u9fffA-Za-z]", text)
            ):
                fields["merchant_name"] = to_traditional(line.text.strip())
    for text in row_texts:
        if "date" not in fields and (value := parse_date(text)):
            fields["date"] = value
        if "time" not in fields and (value := parse_time(text)):
            fields["time"] = value
        match = re.search(r"統一編號|統編|賣方", text)
        if match and (tax_id := parse_tax_id(text[match.end() :])):
            fields["merchant_tax_id"] = tax_id
    total_row = len(rows)
    for label in _TOTALS:
        candidates = [
            (index, _row_amount(rows[index], label))
            for index, text in enumerate(row_texts)
            if not any(_label_match(payment, text) for payment in _PAYMENTS)
            and not re.search(r"小計|subtotal|sub total", text, re.I)
        ]
        found = next(((index, value) for index, value in candidates if value is not None), None)
        if found:
            total_row, fields["total"] = found
            break
    for key, labels in (("subtotal", ("小計", "subtotal")), ("tax", ("稅額", "營業稅", "tax"))):
        for row, text in zip(rows, row_texts):
            if key == "tax" and re.search(r"tax\s*(?:id|number)", text, re.I):
                continue
            for label in labels:
                if (amount := _row_amount(row, label)) is not None:
                    fields[key] = amount
                    break
    items = []
    for index, row in enumerate(rows[:total_row]):
        text = row_texts[index]
        if max(line.box[3] for line in row) <= 0.2:
            continue
        if (
            parse_date(text)
            or parse_time(text)
            or re.search(r"小計|稅額|營業稅|subtotal|\btax\b|[A-Z]{2}-?\d{8}", text, re.I)
            or any(_label_match(payment, text) for payment in _PAYMENTS)
        ):
            continue
        if item := parse_item_row(row):
            items.append(item)
    fields["items"] = items
    missing = [f"{key}_missing" for key in ("date", "total") if fields.get(key) is None]
    mean_score = sum(line.score for line in lines) / len(lines) if lines else 0
    confidence = min(0.9, max(0.05, 0.3 + 0.4 * mean_score - 0.1 * len(missing)))
    return ExtractionResult(fields, confidence, missing)


def resolve_fields(
    lines: list[OcrLine],
    image: np.ndarray,
    templates: TemplateHook | None = None,
) -> ExtractionResult:
    if templates is not None:
        result = templates.extract(lines, image)
        if result is not None:
            return result
    return parse_generic(lines)
