"""Pure OCR geometry strategies and template field extraction."""

import re
import statistics
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..extractors.base import ExtractionResult
from ..extractors.ocr import OcrLine, group_rows, last_money, parse_generic, parse_item_row
from ..models import CustomField, LineItem
from ..normalize import (
    parse_date,
    parse_date_detail,
    parse_money,
    parse_tax_id,
    parse_time,
    to_traditional,
)
from .matcher import compact, has_label, label_span
from .model import Template


def center(line: OcrLine) -> tuple[float, float]:
    return (line.box[0] + line.box[2]) / 2, (line.box[1] + line.box[3]) / 2


def inside(line: OcrLine, box: list[float]) -> bool:
    x, y = center(line)
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def clean_value(text: str, stop_labels: Sequence[str] = ()) -> str | None:
    ends = [span[0] for label in stop_labels if (span := label_span(text, label))]
    if ends:
        text = text[: min(ends)]
    text = to_traditional(unicodedata.normalize("NFKC", text)).strip(" \t\r\n:：()/／_＿")
    return text if any(c.isalnum() for c in text) else None


def normalize_value(value: Any, field_type: str) -> Any:
    if field_type == "items":
        return value if isinstance(value, list) and value else None
    if value is None:
        return None
    text = clean_value(str(value))
    if text is None:
        return None
    if field_type == "invoice_number":
        match = re.fullmatch(r"([A-Za-z]{2})\s*-?\s*(\d{8})", text)
        return "".join(match.groups()).upper() if match else None
    if field_type == "period":
        match = re.fullmatch(r"(\d{2,3})\s*年\s*(\d{1,2})\s*[-~]\s*(\d{1,2})\s*月", text)
        if match and 1 <= int(match[2]) <= int(match[3]) <= 12:
            return f"{match[1]}年{int(match[2]):02}-{int(match[3]):02}月"
        return None
    parser = {
        "number": parse_money,
        "money": parse_money,
        "date": parse_date,
        "time": parse_time,
        "tax_id": parse_tax_id,
    }.get(field_type)
    return parser(text) if parser else text


def _skip(text: str, locator: dict) -> bool:
    if has_label(text, locator.get("skip", [])):
        return True
    chars = [c for c in text if c.isalnum()]
    return bool(
        locator.get("skip_digits") and chars and sum(c.isdigit() for c in chars) / len(chars) > 0.5
    )


def _near_distance(line: OcrLine, labels: list[str], lines: list[OcrLine]) -> float:
    x, y = center(line)
    return (
        min(
            ((x - center(other)[0]) ** 2 + (y - center(other)[1]) ** 2) ** 0.5
            for other in lines
            if has_label(other.text, labels)
        )
        if any(has_label(other.text, labels) for other in lines)
        else float("inf")
    )


def _direction_candidates(
    anchor: OcrLine, lines: list[OcrLine], direction: str, gap: float
) -> list[OcrLine]:
    ax, ay = center(anchor)
    candidates = []
    for line in lines:
        if line is anchor:
            continue
        x, y = center(line)
        horizontal = max(anchor.box[0], line.box[0]) < min(anchor.box[2], line.box[2])
        vertical = max(anchor.box[1], line.box[1]) < min(anchor.box[3], line.box[3])
        if direction == "right":
            valid, distance = x > ax and vertical, max(0, line.box[0] - anchor.box[2])
        elif direction == "left":
            valid, distance = x < ax and vertical, max(0, anchor.box[0] - line.box[2])
        elif direction == "above":
            valid, distance = y < ay and horizontal, max(0, anchor.box[1] - line.box[3])
        else:
            valid, distance = y > ay and horizontal, max(0, line.box[1] - anchor.box[3])
        if valid and distance <= gap:
            candidates.append(
                (distance, abs(x - ax) if direction in {"above", "below"} else abs(y - ay), line)
            )
    return [c[2] for c in sorted(candidates, key=lambda c: c[:2])]


def _collect(anchor: OcrLine, lines: list[OcrLine], direction: str, locator: dict) -> str | None:
    width = anchor.box[2] - anchor.box[0]
    _, ay = center(anchor)
    gap = locator.get("max_gap", 0.3)
    candidates = [
        line
        for line in lines
        if line is not anchor
        and not _skip(line.text, locator)
        and anchor.box[0] - width * 0.5 <= center(line)[0] <= anchor.box[2] + width * 0.5
        and 0 < (ay - center(line)[1] if direction == "above" else center(line)[1] - ay) <= gap
    ]
    if not candidates:
        return None
    height = statistics.median(line.box[3] - line.box[1] for line in candidates)
    candidates.sort(key=lambda line: abs(center(line)[1] - ay))
    band = [candidates[0]]
    for line in candidates[1:]:
        if min(abs(center(line)[1] - center(item)[1]) for item in band) > height:
            break
        band.append(line)
    return "".join(line.text for line in sorted(band, key=lambda line: center(line)[0]))


def anchor_values(lines: list[OcrLine], locator: dict) -> Iterator[str | None]:
    occurrences = []
    for line in lines:
        for label in locator["labels"]:
            span = label_span(line.text, label)
            if span is None:
                continue
            near = locator.get("near")
            distance = _near_distance(line, near["labels"], lines) if near else 0
            if near and distance > near.get("max_distance", 0.3):
                continue
            avoid = locator.get("not_near")
            if avoid and _near_distance(line, avoid["labels"], lines) <= avoid.get(
                "max_distance", 0.3
            ):
                continue
            approximate = compact(label) not in compact(line.text)
            occurrences.append((approximate, distance, line.box[1], line.box[0], line, span))
            break
    if any(not entry[0] for entry in occurrences):
        occurrences = [entry for entry in occurrences if not entry[0]]
    occurrences.sort(key=lambda entry: entry[2:4], reverse=locator.get("occurrence") == "last")
    occurrences.sort(key=lambda entry: entry[1])
    direction = locator.get("direction", "right")
    directions = {
        "right_or_below": ["right", "below"],
        "left_or_right": ["left", "right"],
        "above_or_below": ["above", "below"],
    }.get(direction, [direction])
    for *_, anchor, span in occurrences:
        text = clean_value(anchor.text[span[1] :], locator.get("stop_labels", []))
        if text and not _skip(text, locator):
            yield text
        for direction in directions:
            if locator.get("collect") and direction in {"above", "below"}:
                value = _collect(anchor, lines, direction, locator)
                if value:
                    yield clean_value(value, locator.get("stop_labels", []))
                continue
            for candidate in _direction_candidates(
                anchor, lines, direction, locator.get("max_gap", 0.3)
            ):
                if _skip(candidate.text, locator):
                    continue
                yield clean_value(candidate.text, locator.get("stop_labels", []))


def _character_match(
    chars: list[tuple[str, float]], needle: str, claimed: set[int], *, fuzzy: bool = False
) -> list[int] | None:
    substitutions = range(len(needle)) if fuzzy else [None]
    matches = []
    for substituted in substitutions:
        for first in range(len(chars)):
            indices, start = [], first
            for position, char in enumerate(needle):
                index = next(
                    (
                        i
                        for i in range(start, len(chars))
                        if i not in claimed and (position == substituted or chars[i][0] == char)
                    ),
                    None,
                )
                if index is None:
                    break
                indices.append(index)
                start = index + 1
            else:
                matches.append(indices)
    return (
        min(matches, key=lambda indices: (chars[indices[-1]][1] - chars[indices[0]][1], indices[0]))
        if matches
        else None
    )


def _header_columns(row: list[OcrLine], locator: dict) -> dict[str, float]:
    chars = []
    for line in row:
        text = compact(line.text)
        width = (line.box[2] - line.box[0]) / max(1, len(text))
        chars.extend((char, line.box[0] + (i + 0.5) * width) for i, char in enumerate(text))
    chars.sort(key=lambda char: char[1])
    claimed = set()
    columns = {}
    labels_by_key = dict(locator["columns"])
    labels_by_key.update(
        {f"ignore_{i}": labels for i, labels in enumerate(locator.get("ignore_columns", []))}
    )
    pending = []
    for key, labels in labels_by_key.items():
        found = None
        for fuzzy in (False, True):
            for label in labels:
                needle = compact(label)
                if fuzzy and len(needle) < 3:
                    continue
                found = _character_match(chars, needle, claimed, fuzzy=fuzzy)
                if found:
                    break
            if found:
                break
        if found:
            columns[key] = statistics.mean(chars[i][1] for i in found)
            claimed.update(found)
        elif key != "name":
            pending.append((key, labels))
    for key, labels in pending:
        index = next(
            (
                i
                for label in labels
                for i, char in enumerate(chars)
                if i not in claimed and char[0] == compact(label)[-1]
            ),
            None,
        )
        if index is not None:
            columns[key] = chars[index][1]
            claimed.add(index)
    return columns


def parse_table(lines: list[OcrLine], locator: dict) -> list[LineItem]:
    rows = group_rows(lines)
    for index, row in enumerate(rows):
        columns = _header_columns(row, locator)
        if "name" in columns and len(columns) >= min(2, len(locator["columns"])):
            break
    else:
        return []
    items = []
    for row in rows[index + 1 :]:
        if has_label(" ".join(line.text for line in row), locator.get("stop_labels", [])):
            break
        cells: dict[str, list[str]] = {}
        for line in row:
            x, _ = center(line)
            if x < columns["name"] and re.fullmatch(r"\d{1,2}", line.text.strip()):
                continue
            key = min(columns, key=lambda key: abs(columns[key] - x))
            if not key.startswith("ignore_"):
                cells.setdefault(key, []).append(line.text)
        name = clean_value(" ".join(cells.get("name", [])))
        if not name or not re.search(r"[^\W\d_]|[\u3400-\u9fff]", name):
            continue
        values = {
            key: parse_money(" ".join(cells.get(key, [])))
            for key in ("quantity", "unit_price", "amount")
        }
        if any(value is not None for value in values.values()):
            items.append(LineItem(name, **values))
    return items


def locate_values(lines: list[OcrLine], locator: dict, field_type: str) -> Iterator[Any]:
    strategy = locator["strategy"]
    rows = group_rows(lines)
    if strategy == "anchor":
        yield from anchor_values(lines, locator)
    elif strategy == "region":
        selected = [line for line in lines if inside(line, locator["box"])]
        if field_type == "items":
            yield [item for row in group_rows(selected) if (item := parse_item_row(row))]
        else:
            text = " ".join(line.text for row in group_rows(selected) for line in row)
            if locator.get("cjk_only"):
                text = "".join(re.findall(r"[\u3400-\u9fff]+", text))
            yield text
    elif strategy == "row":
        active = not locator.get("after")
        selected_rows = []
        for row in rows:
            selected = [
                line for line in row if "within" not in locator or inside(line, locator["within"])
            ]
            text = " ".join(line.text for line in selected)
            if not active:
                if has_label(text, locator["after"]):
                    active = True
                continue
            if has_label(text, locator.get("before", [])):
                break
            if all(has_label(text, [label]) for label in locator.get("contains_all", [])) and (
                not locator.get("contains_any") or has_label(text, locator["contains_any"])
            ):
                normalized = to_traditional(unicodedata.normalize("NFKC", text))
                if locator.get("date_required") and parse_date(normalized) is None:
                    continue
                if "pattern" in locator and not re.search(locator["pattern"], normalized):
                    continue
                if "exclude_pattern" in locator and re.search(
                    locator["exclude_pattern"], normalized
                ):
                    continue
                if field_type == "items":
                    selected_rows.append(selected)
                else:
                    yield last_money(text) if locator.get("last_money") else text
                if locator.get("first"):
                    break
        if field_type == "items":
            yield [item for row in selected_rows if (item := parse_item_row(row))]
    elif strategy == "regex":
        text = "\n".join(
            " ".join(to_traditional(unicodedata.normalize("NFKC", line.text)) for line in row)
            for row in group_rows(
                [
                    line
                    for line in lines
                    if "within" not in locator or inside(line, locator["within"])
                ]
            )
        )
        for match in re.finditer(locator["pattern"], text):
            yield match.group(locator.get("group", 1))
    elif strategy == "table":
        yield parse_table(lines, locator)


@dataclass
class TemplateResult(ExtractionResult):
    report: dict[str, str | None] = field(default_factory=dict)


def apply_template(
    template: Template, lines: list[OcrLine], image: np.ndarray | None = None
) -> TemplateResult:
    values, report, warnings = {}, {}, []
    for spec in template.fields:
        value, hit, date_warnings = None, None, []
        for locator in spec["locate"]:
            for raw in locate_values(lines, locator, spec["type"]):
                value = normalize_value(raw, spec["type"])
                if value is not None and "reject_pattern" in spec and re.search(
                    spec["reject_pattern"], str(value)
                ):
                    value = None
                if value is not None:
                    if spec["type"] == "date":
                        _, date_warnings = parse_date_detail(str(raw))
                    break
            if value is not None:
                hit = locator["strategy"]
                if locator.get("warning"):
                    warnings.append(locator["warning"])
                break
        if value is None and spec.get("default") is not None:
            value = normalize_value(spec["default"], spec["type"])
            if value is not None:
                hit = "default"
                warnings.append(f"template_default_used:{spec['key']}")
                if spec["type"] == "date":
                    _, date_warnings = parse_date_detail(str(spec["default"]))
        warnings.extend(date_warnings)
        if value is None and not spec.get("optional"):
            warnings.append(f"template_field_missing:{spec['key']}")
        values[spec["key"]], report[spec["key"]] = value, hit
    generic = parse_generic(lines) if template.data.get("generic_fallback", True) else None
    fields = dict(generic.fields) if generic else {}
    fields.update(
        {
            key: value
            for key, value in template.data.get("defaults", {}).items()
            if value is not None and key != "item_name"
        }
    )
    custom, item_name = [], template.data.get("defaults", {}).get("item_name")
    for spec in template.fields:
        value = values[spec["key"]]
        target = spec.get("target", "custom")
        if target == "custom":
            custom.append(CustomField(spec["key"], spec["label"], value))
        elif target == "item_name":
            item_name = value
        elif target != "check" and value is not None:
            fields[target] = value
        other = values.get(spec.get("cross_check"))
        if value is not None and other is not None and value != other:
            warnings.append(f"cross_check_mismatch:{spec['key']}")
    template_items = any(
        spec.get("target") == "items" and values[spec["key"]] for spec in template.fields
    )
    if item_name and not template_items:
        fields["items"] = [LineItem(item_name, quantity=1, amount=fields.get("total"))]
    if (
        generic
        and "date_year_inferred" in generic.warnings
        and not any(
            spec.get("target") == "date" and values[spec["key"]] for spec in template.fields
        )
        and fields.get("date") == generic.fields.get("date")
    ):
        warnings.append("date_year_inferred")
    fields.update(template_id=template.id, template_name=template.name, custom_fields=custom)
    confidence = 0.75 - 0.1 * sum(fields.get(key) is None for key in ("date", "total"))
    return TemplateResult(fields, confidence, warnings, report)
