import json
import threading
from http.server import ThreadingHTTPServer

from examples.make_sample_einvoice import make_sample
from examples.webhook_receiver import make_handler
from ezreceipt import WebhookSink, scan_image


def test_sample_and_stdlib_receiver(tmp_path, validate_payload):
    image = tmp_path / "合成電子發票.png"
    output = tmp_path / "接收紀錄.jsonl"
    make_sample(image)
    receipt = scan_image(image, mode="einvoice")
    assert receipt is not None
    assert receipt.total == 340 and len(receipt.items) == 2
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(output))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        sink = WebhookSink(f"http://127.0.0.1:{server.server_port}/receipts", backoff=0)
        sink.send(receipt)
        sink.send(receipt)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()] == [
        validate_payload(receipt)
    ]
