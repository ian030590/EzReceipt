import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from ezreceipt.cli import main

from .test_templates import line


@pytest.mark.parametrize("verbose", [False, True])
@pytest.mark.parametrize(
    "command,expected",
    [
        (
            ["validate", "invalid.json"],
            "INVALID: invalid.json: template: unknown keys: unknown",
        ),
        (["show", "missing"], "ERROR: Unknown template: missing"),
        (["test", "missing", "bad.png"], "ERROR: Unknown template: missing"),
        (
            ["test", "tw_post_stamp_purchase", "bad.png"],
            "ERROR: Cannot read image: bad.png",
        ),
        (["copy-builtin", "missing"], "ERROR: Unknown built-in template: missing"),
        (["new", "--image", "bad.png"], "ERROR: Cannot read image: bad.png"),
    ],
)
def test_template_errors_are_concise(tmp_path, monkeypatch, capsys, command, expected, verbose):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EZRECEIPT_TEMPLATES_DIR", str(tmp_path))
    (tmp_path / "invalid.json").write_text('{"unknown": true}', encoding="utf-8")
    (tmp_path / "bad.png").write_bytes(b"not an image")
    assert main(["template", *command, *(["-v"] if verbose else [])]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    if verbose:
        assert captured.err.startswith(expected + "\n")
        assert "Traceback (most recent call last):" in captured.err
    else:
        assert captured.err == expected + "\n"


def test_template_error_reason_stays_on_one_line(tmp_path, monkeypatch, capsys):
    def fail(**kwargs):
        raise RuntimeError("cannot start\nno display")

    monkeypatch.setattr("ezreceipt.templates.annotate.new_template", fail)
    assert main(["template", "new", "--image", "sample.png", "--templates-dir", str(tmp_path)]) == 1
    assert capsys.readouterr().err == "ERROR: cannot start no display\n"


def test_template_commands(tmp_path, capsys, monkeypatch):
    assert main(["template", "list"]) == 0
    assert "tw_post_stamp_purchase" in capsys.readouterr().out
    assert main(["template", "show", "tw_post_stamp_purchase"]) == 0
    assert json.loads(capsys.readouterr().out)["format_version"] == 1
    assert (
        main(
            ["template", "copy-builtin", "tw_post_stamp_purchase", "--templates-dir", str(tmp_path)]
        )
        == 0
    )
    path = tmp_path / "tw_post_stamp_purchase" / "template.json"
    assert path.exists()
    capsys.readouterr()
    assert main(["template", "validate", str(path)]) == 0
    assert capsys.readouterr().out.startswith("OK:")
    monkeypatch.setenv("EZRECEIPT_TEMPLATES_DIR", str(tmp_path))
    assert main(["template", "list"]) == 0
    assert "user" in capsys.readouterr().out
    assert main(["template", "show", "missing"]) == 1
    assert main(["template", "copy-builtin", "tw_post_stamp_purchase"]) == 1
    path.write_text('{"unknown": true}', encoding="utf-8")
    assert main(["template", "validate", str(path)]) == 1


def test_template_new_routes_without_display(tmp_path, monkeypatch, capsys):
    calls = []
    path = tmp_path / "synthetic" / "template.json"
    monkeypatch.setattr(
        "ezreceipt.templates.annotate.new_template", lambda **kwargs: calls.append(kwargs) or path
    )
    assert (
        main(
            [
                "template",
                "new",
                "--image",
                "sample.jpg",
                "--templates-dir",
                str(tmp_path),
                "--id",
                "synthetic",
                "--name",
                "合成模板",
            ]
        )
        == 0
    )
    assert calls[0] == {
        "image": "sample.jpg",
        "camera": None,
        "templates_dir": str(tmp_path),
        "template_id": "synthetic",
        "name": "合成模板",
    }
    assert "template.json" in capsys.readouterr().out
    monkeypatch.setenv("EZRECEIPT_TEMPLATES_DIR", str(tmp_path))
    assert main(["template", "new", "--camera", "2"]) == 0
    assert calls[-1]["camera"] == 2
    monkeypatch.delenv("EZRECEIPT_TEMPLATES_DIR")
    assert main(["template", "new", "--image", "sample.jpg"]) == 1


def test_template_test_real_pipeline_with_fake_ocr(tmp_path, monkeypatch, capsys):
    path = tmp_path / "synthetic.png"
    cv2.imencode(".png", np.full((1200, 800, 3), (220, 70, 180), np.uint8))[1].tofile(path)
    lines = [
        line("購買票品證明單", 0.2, 0.1, 0.5),
        line("NT: 500", 0.1, 0.45),
        line("104.05.06-13", 0.76, 0.42),
    ]
    def recognize(self, image):
        assert np.all(image == 70)
        return lines

    monkeypatch.setattr("ezreceipt.extractors.ocr.OcrEngine.recognize", recognize)
    assert main(["template", "test", "tw_post_stamp_purchase", str(path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["receipt"]["template_id"] == "tw_post_stamp_purchase"
    assert output["receipt"]["total"] == 500
    assert output["strategies"]["total"] == "regex"


def test_scan_and_watch_template_flags(tmp_path, monkeypatch, capsys):
    path = tmp_path / "synthetic.jpg"
    cv2.imencode(".jpg", np.full((1200, 800, 3), 255, np.uint8))[1].tofile(path)
    monkeypatch.setattr(
        "ezreceipt.extractors.ocr.OcrEngine.recognize",
        lambda self, image: [line("總計 500", 0.1, 0.8)],
    )
    assert (
        main(
            [
                "scan",
                str(path),
                "--extractor",
                "ocr",
                "--templates-dir",
                str(tmp_path),
                "--template",
                "tw_post_stamp_purchase",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["template_id"] == "tw_post_stamp_purchase"
    assert main(["scan", str(path), "--extractor", "ocr", "--no-templates"]) == 0
    assert json.loads(capsys.readouterr().out)["template_id"] is None
    scanners = []
    monkeypatch.setattr(
        "ezreceipt.cli.ReceiptScanner",
        lambda **kwargs: scanners.append(kwargs) or SimpleNamespace(run=lambda **kwargs: None),
    )
    monkeypatch.setenv("EZRECEIPT_TEMPLATES_DIR", str(tmp_path))
    assert main(["watch", "--template", "tw_post_stamp_purchase", "--no-preview"]) == 0
    assert scanners[0]["pipeline"].template == "tw_post_stamp_purchase"
    assert scanners[0]["pipeline"].templates.templates_dir == str(tmp_path)
