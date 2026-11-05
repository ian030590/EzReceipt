"""QR-first recognition and merging, with a template hook before generic parsing."""

import logging
from pathlib import Path
from typing import Any

import numpy as np

from .extractors import einvoice
from .extractors.ocr import OcrEngine, TemplateHook, resolve_fields
from .imaging import ocr_input, preprocess_ocr, read_image
from .models import Receipt
from .templates.align import match_reference
from .templates.engine import apply_template
from .templates.matcher import match_template
from .templates.registry import TemplateRegistry

logger = logging.getLogger(__name__)


def validate_totals(fields: dict[str, Any], warnings: list[str]) -> None:
    amounts = [item.amount for item in fields.get("items", []) if item.amount is not None]
    if amounts:
        amount = round(sum(amounts), 2)
        if fields.get("total") is None:
            fields["total"] = amount
            warnings.append("total_from_items")
        elif abs(fields["total"] - amount) > 0.5 and not (
            fields.get("subtotal") is not None
            and fields.get("tax") is not None
            and abs(fields["subtotal"] - amount) <= 0.5
            and abs(fields["subtotal"] + fields["tax"] - fields["total"]) <= 0.5
        ):
            warnings.append("items_total_mismatch")


class Pipeline:
    def __init__(
        self,
        mode: str = "auto",
        ocr_engine: Any = None,
        templates: TemplateHook | TemplateRegistry | None = None,
        *,
        templates_dir: str | Path | None = None,
        template: str | None = None,
        use_templates: bool = True,
    ) -> None:
        if mode not in ("auto", "einvoice", "ocr"):
            raise ValueError("mode must be auto, einvoice or ocr")
        self.mode = mode
        self.ocr_engine = ocr_engine if ocr_engine is not None else OcrEngine()
        if template and not use_templates:
            raise ValueError("template and use_templates=False are mutually exclusive")
        self.templates = (
            (templates if templates is not None else TemplateRegistry(templates_dir))
            if use_templates
            else None
        )
        self.template = template
        self.last_strategy_report: dict[str, str | None] = {}
        if template:
            if not isinstance(self.templates, TemplateRegistry):
                raise ValueError("A forced template requires a TemplateRegistry")
            self.templates.get(template)

    def warmup(self) -> None:
        if self.mode != "einvoice" and hasattr(self.ocr_engine, "warmup"):
            self.ocr_engine.warmup()

    def process(self, image_bgr: np.ndarray) -> Receipt | None:
        if image_bgr is None or image_bgr.size == 0:
            raise ValueError("image must not be empty")
        qr = einvoice.extract(image_bgr) if self.mode != "ocr" else None
        ocr = None
        self.last_strategy_report = {}
        if self.mode != "einvoice":
            try:
                image = preprocess_ocr(image_bgr)
                registry = self.templates if isinstance(self.templates, TemplateRegistry) else None
                candidates = (
                    ([registry.get(self.template)] if self.template else registry.templates)
                    if registry
                    else []
                )
                alignment = match_reference(candidates, image)
                if alignment:
                    image = alignment.image
                lines = self.ocr_engine.recognize(ocr_input(image))
                chosen = (
                    alignment.template
                    if alignment
                    else registry.get(self.template)
                    if registry and self.template
                    else match_template(candidates, lines)
                )
                if chosen:
                    ocr = apply_template(chosen, lines, image)
                    self.last_strategy_report = ocr.report
                else:
                    ocr = resolve_fields(lines, image, None if registry else self.templates)
            except Exception:
                logger.exception("Local OCR failed")
        fields = dict(ocr.fields) if ocr else {}
        if qr:
            for key, value in qr.fields.items():
                if key == "items" and "einvoice_items_incomplete" in qr.warnings:
                    if len(fields.get("items", [])) > len(value):
                        continue
                if value is not None:
                    fields[key] = value
            confidence = qr.confidence
            warnings = list(dict.fromkeys(qr.warnings + (ocr.warnings if ocr else [])))
        elif ocr and (fields.get("total") is not None or fields.get("items") or fields.get("date")):
            confidence, warnings = ocr.confidence, list(ocr.warnings)
        else:
            logger.info("No QR, date, total or items found")
            return None
        if ocr is None and self.mode == "auto":
            warnings.append("ocr_failed")
        validate_totals(fields, warnings)
        for key in ("date", "total"):
            warning = f"{key}_missing"
            if fields.get(key) is None and warning not in warnings:
                warnings.append(warning)
            elif fields.get(key) is not None and warning in warnings:
                warnings.remove(warning)
        return Receipt(
            "tw_einvoice_qr" if qr else "local_ocr",
            **fields,
            confidence=confidence,
            warnings=list(dict.fromkeys(warnings)),
        )


def scan_image(path_or_array: str | Path | np.ndarray, **pipeline_kwargs: Any) -> Receipt | None:
    image = read_image(path_or_array) if isinstance(path_or_array, (str, Path)) else path_or_array
    return Pipeline(**pipeline_kwargs).process(image)
