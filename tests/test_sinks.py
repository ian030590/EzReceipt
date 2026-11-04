import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from ezreceipt.models import Receipt
from ezreceipt.sinks import DeliveryError, JsonlSink, StdoutSink, WebhookSink


@pytest.fixture
def server():
    calls, statuses = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append((dict(self.headers), json.loads(body)))
            status = statuses.pop(0) if statuses else 204
            self.send_response(status)
            if status == 302:
                self.send_header("Location", "/redirected")
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/receipts", calls, statuses
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def test_webhook_headers_retry(server, validate_payload):
    url, calls, statuses = server
    statuses.extend([500, 429, 201])
    receipt = Receipt("local_ocr", total=35, merchant_name="合成店")
    WebhookSink(url, token="test-token", backoff=0).send(receipt)
    assert len(calls) == 3
    headers, body = calls[0]
    assert body == validate_payload(receipt)
    assert headers["Authorization"] == "Bearer test-token"
    assert headers["Idempotency-Key"] == receipt.receipt_id
    assert headers["X-Ezreceipt-Event"] == "receipt.captured"
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["User-Agent"] == "EzReceipt/0.1.0"
    assert all(call[1]["receipt_id"] == receipt.receipt_id for call in calls)


def test_no_retry_400_and_exhausted(server):
    url, calls, statuses = server
    statuses.append(400)
    with pytest.raises(DeliveryError):
        WebhookSink(url, backoff=0).send(Receipt("local_ocr"))
    assert len(calls) == 1
    statuses.extend([500] * 3)
    with pytest.raises(DeliveryError):
        WebhookSink(url, retries=2, backoff=0).send(Receipt("local_ocr"))
    assert len(calls) == 4


def test_connection_backoff(monkeypatch):
    sleeps = []
    monkeypatch.setattr("ezreceipt.sinks.time.sleep", sleeps.append)

    def fail(*args, **kwargs):
        raise URLError("offline fake")

    monkeypatch.setattr("ezreceipt.sinks.build_opener", lambda *args: SimpleNamespace(open=fail))
    with pytest.raises(DeliveryError):
        WebhookSink("http://localhost/", retries=3, backoff=0.5).send(Receipt("local_ocr"))
    assert sleeps == [0.5, 1, 2]


def test_json_outputs(tmp_path, validate_payload):
    receipt = Receipt("local_ocr", merchant_name="合成店")
    path = tmp_path / "中文" / "receipts.jsonl"
    sink = JsonlSink(path)
    sink.send(receipt)
    sink.send(receipt)
    assert [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] == [
        validate_payload(receipt)
    ] * 2
    stream = io.StringIO()
    StdoutSink(stream).send(receipt)
    assert "合成店" in stream.getvalue()
    assert json.loads(stream.getvalue()) == validate_payload(receipt)


def test_redirect_is_not_followed(server):
    url, calls, statuses = server
    statuses.append(302)
    with pytest.raises(DeliveryError, match="HTTP 302"):
        WebhookSink(url, token="test-token").send(Receipt("local_ocr"))
    assert len(calls) == 1
