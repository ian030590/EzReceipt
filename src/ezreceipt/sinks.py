"""JSONL output and retrying stdlib webhook delivery."""

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Protocol, TextIO
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import __version__
from .models import Receipt


class Sink(Protocol):
    def send(self, receipt: Receipt) -> None: ...


class DeliveryError(RuntimeError):
    """The webhook did not acknowledge the receipt."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _json(receipt: Receipt) -> str:
    return json.dumps(receipt.to_payload(), ensure_ascii=False, allow_nan=False)


class StdoutSink:
    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream
        self._lock = threading.Lock()

    def send(self, receipt: Receipt) -> None:
        with self._lock:
            stream = self.stream if self.stream is not None else sys.stdout
            stream.write(_json(receipt) + "\n")
            stream.flush()


class JsonlSink:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def send(self, receipt: Receipt) -> None:
        line = _json(receipt)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


class WebhookSink:
    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        timeout: float = 10,
        retries: int = 3,
        backoff: float = 1.0,
    ) -> None:
        self.url = url or os.environ.get("EZRECEIPT_WEBHOOK_URL")
        self.token = token if token is not None else os.environ.get("EZRECEIPT_WEBHOOK_TOKEN")
        if not self.url or not self.url.lower().startswith(("http://", "https://")):
            raise ValueError("A http(s) webhook URL is required")
        if timeout <= 0 or retries < 0 or backoff < 0:
            raise ValueError("Invalid timeout, retries or backoff")
        self.timeout, self.retries, self.backoff = timeout, retries, backoff
        self._opener = build_opener(_NoRedirect())

    def send(self, receipt: Receipt) -> None:
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": f"EzReceipt/{__version__}",
            "X-EzReceipt-Event": "receipt.captured",
            "Idempotency-Key": receipt.receipt_id,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = _json(receipt).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            request = Request(self.url, data=body, headers=headers, method="POST")
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    if 200 <= response.status < 300:
                        return
                    raise DeliveryError(f"Webhook returned HTTP {response.status}")
            except HTTPError as exc:
                status = exc.code
                exc.close()
                if status != 429 and not 500 <= status < 600:
                    raise DeliveryError(f"Webhook returned HTTP {status}") from exc
                last_error = exc
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
            if attempt < self.retries:
                time.sleep(self.backoff * 2**attempt)
        raise DeliveryError(f"Webhook failed after {self.retries + 1} attempts") from last_error
