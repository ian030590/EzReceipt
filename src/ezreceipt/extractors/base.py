"""Shared extractor output, independent of capture metadata."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractionResult:
    fields: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    warnings: list[str] = field(default_factory=list)
