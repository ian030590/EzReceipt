"""Render synthetic e-invoice QR codes; no real merchant or customer data."""

import argparse
from pathlib import Path

import cv2
import numpy as np
import segno


def make_sample(path: str | Path) -> None:
    header = "AB11223344" + "1131104" + "9999" + "00000144" + "00000154"
    header += "00000000" + "01234567" + "ydXZt4LAN1UHN/j1juVcRA=="
    texts = [header + ":**********:1:2:1:合成商品甲:1:100:", "**合成商品乙:2:120:"]
    matrices = [
        np.asarray(
            list(segno.make(text, micro=False, encoding="utf-8").matrix_iter(scale=6, border=4)),
            dtype=np.uint8,
        )
        for text in texts
    ]
    height = max(matrix.shape[0] for matrix in matrices) + 200
    width = sum(matrix.shape[1] for matrix in matrices) + 100
    image = np.full((height, width, 3), 255, np.uint8)
    cv2.putText(
        image,
        "SYNTHETIC RECEIPT - NOT VALID",
        (30, 50),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 0, 0),
        2,
    )
    x = 30
    for matrix in matrices:
        tile = (1 - matrix) * 255
        image[100 : 100 + tile.shape[0], x : x + tile.shape[1]] = tile[:, :, None]
        x += tile.shape[1] + 40
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        raise OSError("Cannot encode sample")
    encoded.tofile(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", help="Output image path, e.g. sample.png")
    make_sample(parser.parse_args().output)


if __name__ == "__main__":
    main()
