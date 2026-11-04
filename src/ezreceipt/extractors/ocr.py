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
    parse_date_detail,
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
    def __init__(self, engine: Any = None, use_cls: bool = False) -> None:
        self._engine = engine
        self.use_cls = use_cls

    def recognize(self, img_bgr: np.ndarray) -> list[OcrLine]:
        global _ENGINE
        with _ENGINE_LOCK:
            if self._engine is None and _ENGINE is None:
                _ENGINE = _create_engine()
            engine = self._engine if self._engine is not None else _ENGINE
            try:
                result = engine(img_bgr, use_cls=self.use_cls)
            except TypeError as exc:
                if "unexpected keyword argument 'use_cls'" not in str(exc) and (
                    "takes no keyword arguments" not in str(exc)
                ):
                    raise
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
_SUBTOTALS = ("銷售額合計", "銷售額", "小計", "subtotal")
_TAXES = ("營業稅額", "營業稅", "稅額", "tax")


def last_money(text: str) -> float | None:
    text = normalize_text(text)
    text = re.split(r"統一編號|統編|地址|電話|\bTEL\b", text, flags=re.I)[0]
    text = re.sub(r"\d+(?:\.\d+)?\s*(?:[項件個]|pcs\b)", "", text, flags=re.I)
    text = re.sub(r"\d[\d,]*(?:\.\d+)?\s*%", "", text)
    numbers = re.findall(_NUMBER, text)
    if len(numbers) <= 1 or re.search(r"[萬仟千佰百拾十]", text):
        amount = parse_money(text)
        if amount is not None:
            return amount
    return parse_money(numbers[-1]) if numbers else None


def _label_match(label: str, text: str) -> re.Match | None:
    pattern = rf"\b{re.escape(label)}\b" if label.isascii() else re.escape(label)
    return re.search(pattern, text, re.IGNORECASE)


def _amount_after(text: str, label: str) -> float | None:
    match = _label_match(label, text)
    if match is None:
        return None
    suffix = text[match.end() :].strip(" :：=")
    return last_money(suffix)


def _row_amount(row: list[OcrLine], label: str) -> float | None:
    return _amount_after(normalize_text(" ".join(line.text for line in row)), label)


def parse_item_row(row: list[OcrLine]) -> LineItem | None:
    text = " ".join(line.text for line in row).strip()
    text = re.sub(r"\s+(?:應[稅税]|TX|T|N)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+[xX×*]\s+", " ", text)
    match = re.fullmatch(rf"(.+?)\s+({_NUMBER}(?:\s+{_NUMBER}){{0,2}})", text)
    if not match or not re.search(r"[^\W\d_]|[\u3400-\u9fff]", match[1]):
        return None
    name = to_traditional(match[1].strip())
    if re.fullmatch(r"[\d,.\s]+(?:應稅|TX|T|N)?", name, re.I):
        return None
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
    warnings = []
    for line in sorted(lines, key=lambda line: (line.box[1], line.box[0])):
        text = normalize_text(line.text)
        if "date" not in fields:
            value, date_warnings = parse_date_detail(text)
            if value is not None:
                fields["date"] = value
                warnings.extend(date_warnings)
        if "time" not in fields and (value := parse_time(text)):
            fields["time"] = value
        invoice = re.search(r"(?<![A-Z0-9])([A-Z]{2})-?(\d{8})(?!\d)", text.upper())
        if invoice and "invoice_number" not in fields:
            fields["invoice_number"] = "".join(invoice.groups())
        if line.box[1] <= 0.2 and "merchant_name" not in fields:
            title = re.search(r"統一發票|發票證明聯|電子發票|證明聯|收據|證明", text)
            if (
                not title
                and not parse_date(text)
                and not parse_time(text)
                and not invoice
                and not re.search(
                    r"格式|隨機碼|www\.|http|統一編號|統編|賣方|電話|地址|tel|tax"
                    r"|^(?:receipt|invoice)$|^[格式]\s*:?\s*\d*$",
                    text,
                    re.I,
                )
                and re.search(r"[\u3400-\u9fffA-Za-z]", text)
            ):
                fields["merchant_name"] = to_traditional(line.text.strip())
    for text in row_texts:
        if "date" not in fields:
            value, date_warnings = parse_date_detail(text)
            if value is not None:
                fields["date"] = value
                warnings.extend(date_warnings)
        if "time" not in fields and (value := parse_time(text)):
            fields["time"] = value
    identifiers = [
        (match[1], match[2])
        for text in row_texts
        for match in re.finditer(
            r"(買方|買受人|賣方|統一編號|統編|tax\s*(?:id|number))\s*:?\s*(\d{8})(?!\d)",
            text,
            re.I,
        )
        if parse_tax_id(match[2])
    ]
    buyer = next((value for label, value in identifiers if label in {"買方", "買受人"}), None)
    seller = next((value for label, value in identifiers if label == "賣方"), None)
    if buyer:
        fields["buyer_tax_id"] = buyer
    seller = seller or next(
        (
            value
            for label, value in identifiers
            if label not in {"買方", "買受人"} and value != buyer
        ),
        None,
    )
    if seller:
        fields["merchant_tax_id"] = seller
    for label in _TOTALS:
        candidates = [
            (index, _row_amount(rows[index], label))
            for index, text in enumerate(row_texts)
            if not any(_label_match(payment, text) for payment in _PAYMENTS)
            and not re.search(r"銷售額|小計|subtotal|sub total", text, re.I)
        ]
        found = next(((index, value) for index, value in candidates if value is not None), None)
        if found:
            _, fields["total"] = found
            break
    if "total" not in fields:
        currency_amounts = {
            parse_money(match[1])
            for text in row_texts
            for match in re.finditer(
                rf"(?<![A-Za-z])(?:NT\$?|新[臺台]幣)\s*\$?\s*({_NUMBER})", text, re.I
            )
        } - {None}
        if len(currency_amounts) == 1:
            fields["total"] = currency_amounts.pop()
            warnings.append("total_from_currency_mark")
    for key, labels in (("subtotal", _SUBTOTALS), ("tax", _TAXES)):
        for row, text in zip(rows, row_texts):
            if key == "tax" and re.search(r"tax\s*(?:id|number)", text, re.I):
                continue
            for label in labels:
                if (amount := _row_amount(row, label)) is not None:
                    fields[key] = amount
                    break
    items = []
    pending = None
    wrapped = []
    for index, row in enumerate(rows):
        text = row_texts[index]
        quantity = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[*xX×]\s*@?\s*([\d,.]+)", text)
        if quantity:
            pending = (parse_money(quantity[1]), parse_money(quantity[2]))
            wrapped = []
            continue
        if pending:
            ending = re.fullmatch(rf"(?:(.*?)\s+)?({_NUMBER})\s+(?:TX|T|N)", text, re.I)
            if ending:
                name = "".join([*wrapped, ending[1] or ""]).strip()
                if name:
                    items.append(LineItem(name, *pending, parse_money(ending[2])))
                pending, wrapped = None, []
                continue
            if not any(_label_match(label, text) for label in _TOTALS + _SUBTOTALS + _TAXES):
                wrapped.append(text)
                continue
            pending, wrapped = None, []
        if max(line.box[3] for line in row) <= 0.2:
            continue
        if (
            parse_date(text)
            or parse_time(text)
            or re.search(r"統編|統一編號|隨機碼|電話|地址|[A-Z]{2}-?\d{8}", text, re.I)
            or any(_label_match(label, text) for label in _TOTALS + _SUBTOTALS + _TAXES)
            or any(_label_match(payment, text) for payment in _PAYMENTS)
        ):
            continue
        if item := parse_item_row(row):
            items.append(item)
    fields["items"] = items
    missing = [f"{key}_missing" for key in ("date", "total") if fields.get(key) is None]
    mean_score = sum(line.score for line in lines) / len(lines) if lines else 0
    confidence = min(0.9, max(0.05, 0.3 + 0.4 * mean_score - 0.1 * len(missing)))
    return ExtractionResult(fields, confidence, warnings + missing)


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
