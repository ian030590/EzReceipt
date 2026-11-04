"""Offline receipt recognition."""

__version__ = "0.1.0"

from .models import CustomField, LineItem, Receipt
from .pipeline import Pipeline, scan_image
from .scanner import ReceiptScanner
from .sinks import JsonlSink, StdoutSink, WebhookSink

__all__ = [
    "CustomField",
    "JsonlSink",
    "LineItem",
    "Pipeline",
    "Receipt",
    "ReceiptScanner",
    "StdoutSink",
    "WebhookSink",
    "scan_image",
]
