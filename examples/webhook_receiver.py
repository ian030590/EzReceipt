"""Local demo receiver: echo JSON and append it to the supplied JSONL path."""

import argparse
import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logger = logging.getLogger(__name__)


def make_handler(path: str | Path) -> type[BaseHTTPRequestHandler]:
    path = Path(path)
    lock = threading.Lock()
    seen: set[str] = set()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/receipts":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2_000_000:
                    self.send_error(413)
                    return
                payload = json.loads(self.rfile.read(length))
                if (
                    not isinstance(payload, dict)
                    or payload.get("event") != "receipt.captured"
                    or not isinstance(payload.get("receipt_id"), str)
                ):
                    raise ValueError("Not a receipt.captured payload")
            except (ValueError, TypeError, KeyError):
                self.send_error(400)
                return
            with lock:
                if payload["receipt_id"] not in seen:
                    try:
                        line = json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n"
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with path.open("a", encoding="utf-8") as output:
                            output.write(line)
                        sys.stdout.write(line)
                        sys.stdout.flush()
                    except OSError:
                        logger.exception("Cannot write receipt")
                        self.send_error(500)
                        return
                    seen.add(payload["receipt_id"])
            self.send_response(204)
            self.end_headers()

    return Handler


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl", help="Append receipts to this path")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    with ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.jsonl)) as server:
        logger.info("Listening on http://127.0.0.1:%s/receipts", args.port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
