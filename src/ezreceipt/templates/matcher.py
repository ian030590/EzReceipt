"""Whitespace-insensitive labels and ranked template keywords."""

import re

from ..extractors.ocr import OcrLine
from ..normalize import normalize_text
from .model import Template


def compact(text: str) -> str:
    return re.sub(r"\s+", "", normalize_text(text)).casefold()


def label_span(text: str, label: str) -> tuple[int, int] | None:
    chars, positions = [], []
    for index, char in enumerate(text):
        for normalized in compact(char):
            chars.append(normalized)
            positions.append(index)
    haystack, needle = "".join(chars), compact(label)
    if not needle:
        return None
    start = haystack.find(needle)
    if start < 0 and len(needle) >= 3:
        start = next(
            (
                i
                for i in range(len(haystack) - len(needle) + 1)
                if sum(a != b for a, b in zip(haystack[i : i + len(needle)], needle)) <= 1
            ),
            -1,
        )
    if start < 0:
        return None
    return positions[start], positions[start + len(needle) - 1] + 1


def has_label(text: str, labels: list[str]) -> bool:
    return any(label_span(text, label) is not None for label in labels)


def match_template(templates: list[Template], lines: list[OcrLine]) -> Template | None:
    text = compact("\n".join(line.text for line in lines))

    def keyword_matches(label: str) -> bool:
        needle = compact(label)
        return needle in text or len(needle) >= 5 and label_span(text, needle) is not None

    ranked = []
    for template in templates:
        match = template.data.get("match", {})
        if any(compact(label) in text for label in match.get("keywords_none", [])):
            continue
        all_labels, any_labels = match.get("keywords_all", []), match.get("keywords_any", [])
        if not all_labels and not any_labels:
            continue
        if not all(keyword_matches(label) for label in all_labels):
            continue
        hits = [label for label in any_labels if keyword_matches(label)]
        if len(hits) < match.get("min_any", 1 if any_labels else 0):
            continue
        ranked.append(
            (
                (
                    len(all_labels) + len(hits),
                    template.user,
                    sum(len(compact(label)) for label in all_labels + hits),
                ),
                template,
            )
        )
    return max(ranked, key=lambda pair: pair[0])[1] if ranked else None
