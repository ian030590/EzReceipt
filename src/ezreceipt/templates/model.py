"""Version-one template format and strict validation."""

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCALAR_TARGETS = {
    "date",
    "time",
    "merchant_name",
    "merchant_tax_id",
    "buyer_tax_id",
    "invoice_number",
    "subtotal",
    "tax",
    "total",
    "currency",
}
TARGETS = SCALAR_TARGETS | {"items", "item_name", "custom", "check"}
TYPES = {"text", "number", "money", "date", "time", "tax_id", "items"}
DIRECTIONS = {
    "right",
    "below",
    "right_or_below",
    "left",
    "left_or_right",
    "above",
    "above_or_below",
}


def _object(value: Any, allowed: set[str], where: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{where}: expected an object")
    unknown = value.keys() - allowed
    if unknown:
        raise ValueError(f"{where}: unknown keys: {', '.join(sorted(unknown))}")
    return value


def _labels(value: Any, where: str) -> None:
    if not isinstance(value, list) or any(not isinstance(s, str) or not s.strip() for s in value):
        raise ValueError(f"{where}: expected a list of nonempty strings")


def _box(value: Any, where: str) -> None:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 4
        or any(
            not isinstance(n, (int, float))
            or isinstance(n, bool)
            or not math.isfinite(n)
            or not 0 <= n <= 1
            for n in value
        )
        or value[0] >= value[2]
        or value[1] >= value[3]
    ):
        raise ValueError(f"{where}: box must satisfy 0 <= x0 < x1 <= 1, 0 <= y0 < y1 <= 1")


def _distance(value: Any, where: str) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not 0 <= value <= 2
    ):
        raise ValueError(f"{where}: expected a finite distance in 0..2")


def validate_template(data: Any) -> None:
    _object(
        data,
        {
            "format_version",
            "id",
            "name",
            "description",
            "defaults",
            "match",
            "reference_image",
            "notes",
            "fields",
        },
        "template",
    )
    if type(data.get("format_version")) is not int or data["format_version"] != 1:
        raise ValueError("format_version: expected 1")
    if not isinstance(data.get("id"), str) or not re.fullmatch(r"[a-z0-9_]+", data["id"]):
        raise ValueError("id: expected [a-z0-9_]+")
    for key in ("name", "description", "notes"):
        if not isinstance(data.get(key, ""), str) or key == "name" and not data.get(key):
            raise ValueError(f"{key}: expected text")
    defaults = _object(data.get("defaults", {}), SCALAR_TARGETS, "defaults")
    for key, value in defaults.items():
        if key in {"total", "subtotal", "tax"}:
            valid = isinstance(value, (float, int)) and not isinstance(value, bool)
            valid = valid and math.isfinite(value)
        else:
            valid = isinstance(value, str) and bool(value)
        if value is not None and not valid:
            raise ValueError(f"defaults.{key}: invalid scalar value")
    match = _object(
        data.get("match", {}),
        {"keywords_all", "keywords_any", "min_any", "reference_min_inliers"},
        "match",
    )
    for key in ("keywords_all", "keywords_any"):
        _labels(match.get(key, []), f"match.{key}")
    for key, default in (
        ("min_any", 1 if match.get("keywords_any") else 0),
        ("reference_min_inliers", 25),
    ):
        value = match.get(key, default)
        if type(value) is not int or value < 0 or key == "reference_min_inliers" and value < 4:
            raise ValueError(f"match.{key}: invalid integer")
    if match.get("min_any", 0) > len(match.get("keywords_any", [])):
        raise ValueError("match.min_any: exceeds keywords_any count")
    reference = data.get("reference_image")
    if reference is not None and (
        not isinstance(reference, str)
        or not reference
        or Path(reference).is_absolute()
        or ".." in reference.replace("\\", "/").split("/")
    ):
        raise ValueError("reference_image: expected a relative path inside the template folder")
    fields = data.get("fields")
    if not isinstance(fields, list):
        raise ValueError("fields: expected a list")
    keys = set()
    for index, spec in enumerate(fields):
        where = f"fields[{index}]"
        _object(spec, {"key", "label", "type", "target", "locate", "default", "cross_check"}, where)
        key = spec.get("key")
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9_]+", key):
            raise ValueError(f"{where}.key: expected [a-z0-9_]+")
        if key in keys:
            raise ValueError(f"{where}: duplicate key {key}")
        keys.add(key)
        if not isinstance(spec.get("label"), str) or not spec["label"]:
            raise ValueError(f"{where}.label: expected nonempty text")
        target, kind = spec.get("target", "custom"), spec.get("type")
        if (
            not isinstance(target, str)
            or target not in TARGETS
            or not isinstance(kind, str)
            or kind not in TYPES
        ):
            raise ValueError(f"{where}: bad target or type")
        if (spec.get("type") == "items") != (spec.get("target", "custom") == "items"):
            raise ValueError(f"{where}: items type requires items target and vice versa")
        if "default" in spec and spec["default"] is not None:
            value = spec["default"]
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError(f"{where}.default: expected scalar")
            if isinstance(value, (float, int)) and not math.isfinite(value):
                raise ValueError(f"{where}.default: expected finite number")
        locators = spec.get("locate")
        if not isinstance(locators, list) or not locators:
            raise ValueError(f"{where}.locate: expected nonempty list")
        for locator in locators:
            validate_locator(locator, where)
    for spec in fields:
        if "cross_check" in spec and (
            not isinstance(spec["cross_check"], str) or spec["cross_check"] not in keys
        ):
            raise ValueError(f"{spec['key']}.cross_check: unknown field key")


def validate_locator(locator: Any, where: str) -> None:
    options = {
        "region": {"box", "cjk_only"},
        "anchor": {
            "labels",
            "direction",
            "max_gap",
            "near",
            "not_near",
            "skip",
            "skip_digits",
            "occurrence",
            "collect",
            "stop_labels",
        },
        "row": {"contains_all", "contains_any", "within"},
        "regex": {"pattern", "group"},
        "table": {"columns", "stop_labels", "ignore_columns"},
    }
    if (
        not isinstance(locator, dict)
        or not isinstance(locator.get("strategy"), str)
        or locator["strategy"] not in options
    ):
        raise ValueError(f"{where}: unknown locate strategy")
    strategy = locator["strategy"]
    _object(locator, options[strategy] | {"strategy"}, f"{where}.{strategy}")
    for key in ("box", "within"):
        if key in locator:
            _box(locator[key], f"{where}.{key}")
    if strategy == "region" and "box" not in locator:
        raise ValueError(f"{where}: region requires box")
    if "cjk_only" in locator and type(locator["cjk_only"]) is not bool:
        raise ValueError(f"{where}.cjk_only: expected boolean")
    for key in ("labels", "skip", "stop_labels", "contains_all", "contains_any"):
        if key in locator:
            _labels(locator[key], f"{where}.{key}")
    if strategy == "anchor":
        if not locator.get("labels"):
            raise ValueError(f"{where}: anchor requires labels")
        if locator.get("direction", "right") not in DIRECTIONS:
            raise ValueError(f"{where}: bad anchor direction")
        if locator.get("occurrence", "first") not in {"first", "last"}:
            raise ValueError(f"{where}: bad occurrence")
        for key in ("collect", "skip_digits"):
            if key in locator and type(locator[key]) is not bool:
                raise ValueError(f"{where}.{key}: expected boolean")
        for key in ("near", "not_near"):
            if key in locator:
                near = _object(locator[key], {"labels", "max_distance"}, f"{where}.{key}")
                _labels(near.get("labels"), f"{where}.{key}.labels")
                _distance(near.get("max_distance", 0.3), f"{where}.{key}.max_distance")
        _distance(locator.get("max_gap", 0.3), f"{where}.max_gap")
    if strategy == "regex":
        try:
            pattern = re.compile(locator["pattern"])
            group = locator.get("group", 1)
            if type(group) is not int or not 0 <= group <= pattern.groups:
                raise ValueError(f"{where}: regex group does not exist")
        except (KeyError, TypeError, re.error) as exc:
            raise ValueError(f"{where}: invalid regex: {exc}") from exc
    if strategy == "table":
        columns = _object(
            locator.get("columns"), {"name", "quantity", "unit_price", "amount"}, f"{where}.columns"
        )
        if "name" not in columns:
            raise ValueError(f"{where}: table requires name column")
        for labels in columns.values():
            _labels(labels, f"{where}.columns")
        if not isinstance(locator.get("ignore_columns", []), list):
            raise ValueError(f"{where}.ignore_columns: expected a list")
        for labels in locator.get("ignore_columns", []):
            _labels(labels, f"{where}.ignore_columns")


@dataclass
class Template:
    data: dict[str, Any]
    directory: Path | None = None
    user: bool = False
    id: str = field(init=False)
    name: str = field(init=False)

    def __post_init__(self) -> None:
        validate_template(self.data)
        self.id, self.name = self.data["id"], self.data["name"]

    @property
    def fields(self) -> list[dict[str, Any]]:
        return self.data["fields"]

    @property
    def reference_path(self) -> Path | None:
        name = self.data.get("reference_image")
        if self.directory is None:
            return None
        path = self.directory / (name or "reference.jpg")
        if not path.resolve().is_relative_to(self.directory.resolve()):
            raise ValueError("reference_image escapes the template folder")
        return path if path.is_file() else None


def load_template(path: str | Path, *, user: bool = False) -> Template:
    path = Path(path)
    if path.is_dir():
        path /= "template.json"
    try:
        return Template(json.loads(path.read_text(encoding="utf-8")), path.parent, user)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
