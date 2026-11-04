import json
import socket
from importlib.resources import files

import jsonschema
import pytest


@pytest.fixture(scope="session")
def payload_validator():
    schema = json.loads(
        files("ezreceipt").joinpath("schema/receipt-1.0.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


@pytest.fixture(autouse=True)
def validate_all_payloads(monkeypatch, payload_validator):
    from ezreceipt.models import Receipt

    original = Receipt.to_payload

    def checked(receipt):
        payload = original(receipt)
        payload_validator.validate(payload)
        return payload

    monkeypatch.setattr(Receipt, "to_payload", checked)


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    original = socket.getaddrinfo

    def resolve(host, *args, **kwargs):
        if host not in ("127.0.0.1", "::1", "localhost", None):
            raise AssertionError(f"External network is forbidden in tests: {host}")
        return original(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


@pytest.fixture
def validate_payload(payload_validator):

    def validate(receipt):
        payload = receipt.to_payload()
        payload_validator.validate(payload)
        return payload

    return validate


@pytest.fixture
def qr_texts():
    header = "AB11223344" + "1131104" + "9999" + "00000144" + "00000154"
    header += "00000000" + "01234567" + "ydXZt4LAN1UHN/j1juVcRA=="
    return [header + ":**********:2:2:1:乖乖:1:10:", "**拿鐵:2:65:"]


def render_qrs(texts, big5=False):
    import numpy as np
    import segno

    tiles = []
    for text in texts:
        data = text.encode("big5") if big5 else text
        qr = segno.make(data, micro=False, error="m", encoding=None if big5 else "utf-8")
        matrix = np.asarray(list(qr.matrix_iter(scale=5, border=4)), dtype=np.uint8)
        tiles.append((1 - matrix) * 255)
    height = max(tile.shape[0] for tile in tiles)
    width = sum(tile.shape[1] for tile in tiles) + 80
    canvas = np.full((height + 100, width, 3), 255, np.uint8)
    x = 20
    for tile in tiles:
        canvas[40 : 40 + tile.shape[0], x : x + tile.shape[1]] = tile[:, :, None]
        x += tile.shape[1] + 40
    return canvas
