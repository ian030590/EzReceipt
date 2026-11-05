import json
import subprocess
import sys

import cv2

from ezreceipt.cli import main

from .conftest import render_qrs


def test_scan_cli(tmp_path, qr_texts, validate_payload):
    path = tmp_path / "合成收據.png"
    cv2.imencode(".png", render_qrs(qr_texts))[1].tofile(path)
    run = subprocess.run(
        [sys.executable, "-m", "ezreceipt", "scan", str(path), "--extractor", "einvoice"],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert run.returncode == 0, run.stderr
    from ezreceipt import Receipt

    receipt = Receipt.from_payload(json.loads(run.stdout))
    assert validate_payload(receipt)["total"] == 340
    assert "乖乖" in run.stdout


def test_scan_cli_saves_pdf(tmp_path, qr_texts, capsys):
    path = tmp_path / "合成收據.png"
    cv2.imencode(".png", render_qrs(qr_texts))[1].tofile(path)
    pdf_dir = tmp_path / "PDF 輸出"
    assert main(["scan", str(path), "--extractor", "einvoice", "--save-pdf", str(pdf_dir)]) == 0
    payload = json.loads(capsys.readouterr().out)
    pdfs = list(pdf_dir.glob("*.pdf"))
    assert len(pdfs) == 1 and payload["pdf_file"] == str(pdfs[0])
    assert pdfs[0].read_bytes().startswith(b"%PDF")


def test_scan_cli_pdf_failure_returns_error(tmp_path, qr_texts, capsys):
    path = tmp_path / "receipt.png"
    cv2.imencode(".png", render_qrs(qr_texts))[1].tofile(path)
    blocked = tmp_path / "blocked"
    blocked.write_text("")
    assert main(["scan", str(path), "--extractor", "einvoice", "--save-pdf", str(blocked)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["pdf_file"] is None and "pdf_save_failed" in payload["warnings"]


def test_scan_unreadable(tmp_path, capsys):
    path = tmp_path / "bad.jpg"
    path.write_bytes(b"not an image")
    assert main(["scan", str(path), "--extractor", "einvoice"]) == 1
    assert capsys.readouterr().out == ""


def test_scan_nothing_read(tmp_path, qr_texts, capsys):
    image = render_qrs(qr_texts)
    image[:] = 255
    path = tmp_path / "blank.png"
    cv2.imencode(".png", image)[1].tofile(path)
    assert main(["scan", str(path), "--extractor", "einvoice"]) == 1
    assert capsys.readouterr().out == ""


def test_library_import_is_lazy():
    code = (
        "import sys, ezreceipt; "
        "assert all(name not in sys.modules for name in "
        "('rapidocr', 'tkinter', 'PIL.ImageTk')); "
        "assert ezreceipt.__version__ == '0.1.0'"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_watch_options_env_and_cameras(monkeypatch, capsys):
    from ezreceipt.sinks import WebhookSink

    scanners = []

    class Scanner:
        def __init__(self, **kwargs):
            self.options = kwargs
            scanners.append(self)

        def run(self, preview):
            self.preview = preview

    monkeypatch.setattr("ezreceipt.cli.ReceiptScanner", Scanner)
    monkeypatch.setenv("EZRECEIPT_WEBHOOK_URL", "http://localhost/receipts")
    monkeypatch.setenv("EZRECEIPT_WEBHOOK_TOKEN", "test-env-token")
    assert (
        main(
            [
                "watch",
                "--source",
                "demo.mp4",
                "--width",
                "1280",
                "--no-preview",
                "--backend",
                "dshow",
                "--dedupe-window",
                "0",
                "--save-pdf",
                "pdfs",
            ]
        )
        == 0
    )
    scanner = scanners[0]
    assert not scanner.preview
    assert scanner.options["source"] == "demo.mp4"
    assert scanner.options["width"] == 1280 and scanner.options["height"] == 1080
    assert scanner.options["backend"] == "dshow"
    assert scanner.options["dedupe_window_s"] == 0
    assert scanner.options["save_pdf_dir"] == "pdfs"
    sink = scanner.options["sinks"][0]
    assert isinstance(sink, WebhookSink) and sink.token == "test-env-token"
    monkeypatch.setattr("ezreceipt.cli.list_cameras", lambda: [0, 2])
    assert main(["cameras"]) == 0
    assert capsys.readouterr().out == "0\n2\n"
