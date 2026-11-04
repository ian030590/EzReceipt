"""Geometry for invented receipt rows; coordinates are normalized to the page."""

from ezreceipt.extractors.ocr import OcrLine


def row_lines(rows, *, payment_date=False, copy_splits=None):
    lines = []
    spacing = 1 / len(rows)
    for index, row in enumerate(rows):
        cells = row.split(" | ")
        y0, y1 = (index + 0.2) * spacing, (index + 0.8) * spacing
        if payment_date and index == 1:
            lines.append(OcrLine(row, (0.70, y0, 0.90, y1), 0.99))
            continue
        split = copy_splits.get(index, len(cells)) if copy_splits is not None else len(cells)
        for column, text in enumerate(cells):
            if copy_splits is None:
                start, width, position = 0, 1 / len(cells), column
            elif column < split:
                start, width, position = 0, 0.5 / split, column
            else:
                start, width, position = 0.5, 0.5 / (len(cells) - split), column - split
            x0 = start + (position + 0.05) * width
            x1 = start + (position + 0.95) * width
            lines.append(OcrLine(text, (x0, y0, x1, y1), 0.99))
    return lines
