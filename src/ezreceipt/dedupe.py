"""Time-bounded duplicate suppression for a scanner session."""

import time

from .models import Receipt


class Deduplicator:
    def __init__(self, window_s: float = 600) -> None:
        if window_s < 0:
            raise ValueError("window_s must be non-negative")
        self.window_s = window_s
        self._seen: dict[tuple, float] = {}

    def is_duplicate(self, receipt: Receipt, now: float | None = None) -> bool:
        if self.window_s == 0:
            return False
        now = time.monotonic() if now is None else now
        self._seen = {key: seen for key, seen in self._seen.items() if now - seen < self.window_s}
        if receipt.invoice_number:
            fingerprint = (receipt.invoice_number,)
        elif receipt.total is not None:
            fingerprint = (receipt.date, receipt.total, receipt.merchant_name, receipt.template_id)
        else:
            return False
        duplicate = fingerprint in self._seen
        self._seen[fingerprint] = now
        return duplicate
