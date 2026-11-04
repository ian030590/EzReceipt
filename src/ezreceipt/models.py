"""Versioned receipt data shared by extractors and sinks."""

import math
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import uuid4


def json_number(value: float | None, places: int | None = 2) -> int | float | None:
    if value is None:
        return None
    if not math.isfinite(value):
        raise ValueError("Numbers must be finite")
    rounded = Decimal(str(value))
    if places is not None:
        rounded = rounded.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP)
    return int(rounded) if rounded == rounded.to_integral() else float(rounded)


@dataclass
class LineItem:
    name: str
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None

    def __post_init__(self) -> None:
        if self.amount is None and self.quantity is not None and self.unit_price is not None:
            self.amount = self.quantity * self.unit_price
        self.unit_price = json_number(self.unit_price)
        self.amount = json_number(self.amount)


@dataclass
class CustomField:
    key: str
    label: str
    value: str | float | None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9_]+", self.key):
            raise ValueError("Custom field key must match [a-z0-9_]+")


@dataclass
class Receipt:
    source: str
    receipt_id: str = field(default_factory=lambda: str(uuid4()))
    captured_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    merchant_name: str | None = None
    merchant_tax_id: str | None = None
    buyer_tax_id: str | None = None
    invoice_number: str | None = None
    date: str | None = None
    time: str | None = None
    currency: str = "TWD"
    items: list[LineItem] = field(default_factory=list)
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None
    template_id: str | None = None
    template_name: str | None = None
    custom_fields: list[CustomField] = field(default_factory=list)
    confidence: float = 0.5
    warnings: list[str] = field(default_factory=list)
    image_file: str | None = None

    def __post_init__(self) -> None:
        if self.source not in {"tw_einvoice_qr", "local_ocr"}:
            raise ValueError("Unknown receipt source")
        if self.captured_at.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(
            schema_version="1.0",
            event="receipt.captured",
            captured_at=self.captured_at.isoformat(),
            item_count=len(self.items),
        )
        for key in ("subtotal", "tax", "total"):
            payload[key] = json_number(payload[key])
        for item in payload["items"]:
            for key in ("quantity", "unit_price", "amount"):
                item[key] = json_number(item[key], None if key == "quantity" else 2)
        for custom in payload["custom_fields"]:
            if isinstance(custom["value"], (float, int)):
                custom["value"] = json_number(custom["value"], None)
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Receipt":
        if payload.get("schema_version") != "1.0":
            raise ValueError("Unsupported schema_version")
        if payload.get("event") != "receipt.captured":
            raise ValueError("Unsupported event")
        values = {
            key: value
            for key, value in payload.items()
            if key in {spec.name for spec in fields(cls)}
        }
        values["captured_at"] = datetime.fromisoformat(values["captured_at"].replace("Z", "+00:00"))
        values["items"] = [
            LineItem(
                **{
                    key: value
                    for key, value in item.items()
                    if key in {spec.name for spec in fields(LineItem)}
                }
            )
            for item in values["items"]
        ]
        values["custom_fields"] = [
            CustomField(
                **{
                    key: value
                    for key, value in item.items()
                    if key in {spec.name for spec in fields(CustomField)}
                }
            )
            for item in values["custom_fields"]
        ]
        return cls(**values)
