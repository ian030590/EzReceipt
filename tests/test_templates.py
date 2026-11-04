import copy
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from ezreceipt.extractors.ocr import OcrLine
from ezreceipt.normalize import _TRADITIONAL, normalize_text, to_traditional
from ezreceipt.pipeline import Pipeline
from ezreceipt.templates.align import align_to_reference, match_reference
from ezreceipt.templates.engine import apply_template, locate_values, normalize_value, parse_table
from ezreceipt.templates.matcher import has_label, match_template
from ezreceipt.templates.model import Template, load_template
from ezreceipt.templates.registry import TemplateRegistry, copy_builtin


def line(text, x, y, width=0.15, height=0.04):
    return OcrLine(text, (x, y, x + width, y + height))


def template_data():
    return {
        "format_version": 1,
        "id": "synthetic",
        "name": "合成表單",
        "match": {"keywords_any": ["合成表單"], "min_any": 1},
        "fields": [
            {
                "key": "total",
                "label": "總金額",
                "type": "money",
                "target": "total",
                "locate": [{"strategy": "anchor", "labels": ["合計"], "direction": "right"}],
            }
        ],
    }


@pytest.mark.parametrize(
    "path", sorted((Path(__file__).parent / "fixtures").glob("*.json")), ids=lambda path: path.stem
)
def test_acceptance_fixtures(path, validate_payload, fixed_today):
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = [OcrLine(**entry) for entry in data["lines"]]
    registry = TemplateRegistry()
    matched = match_template(registry.templates, lines)
    assert (matched.id if matched else None) == data["expected"]["template_id"]
    if matched:
        extraction = apply_template(matched, lines)
        assert extraction.report
    engine = SimpleNamespace(recognize=lambda image: lines)
    result = Pipeline("ocr", engine, registry).process(np.full((1200, 800, 3), 255, np.uint8))
    payload = validate_payload(result)
    for key, expected in data["expected"].items():
        if key == "items":
            assert len(payload[key]) == len(expected)
            for actual_item, expected_item in zip(payload[key], expected):
                for name, value in expected_item.items():
                    assert actual_item[name] == value
        elif key == "custom_fields":
            custom = {f["key"]: f["value"] for f in payload[key]}
            for name, value in expected.items():
                assert custom[name] == value
        elif key == "warnings_include":
            assert set(expected) <= set(payload["warnings"])
        elif key == "warnings_exclude":
            assert not set(expected) & set(payload["warnings"])
        elif key == "payload_exclude":
            serialized = json.dumps(payload, ensure_ascii=False)
            assert all(value not in serialized for value in expected)
        else:
            assert payload[key] == expected


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda d: d.update(unknown=True), "unknown keys"),
        (lambda d: d.update(format_version=2), "format_version"),
        (lambda d: d.update(id="../escape"), "id"),
        (lambda d: d["fields"][0].update(target="bogus"), "bad target"),
        (lambda d: d["fields"][0].update(type="bogus"), "bad target or type"),
        (lambda d: d["fields"].append(copy.deepcopy(d["fields"][0])), "duplicate key"),
        (lambda d: d["fields"][0].update(cross_check="missing"), "cross_check"),
        (
            lambda d: d["fields"][0].update(locate=[{"strategy": "region", "box": [-1, 0, 1, 1]}]),
            "box must",
        ),
        (
            lambda d: d["fields"][0].update(locate=[{"strategy": "region", "box": [0, 0, 0, 1]}]),
            "box must",
        ),
        (lambda d: d["fields"][0].update(locate=[{"strategy": "region"}]), "requires box"),
        (lambda d: d["fields"][0]["locate"][0].update(typo=1), "unknown keys"),
        (lambda d: d["fields"][0]["locate"][0].update(direction="diagonal"), "bad anchor"),
        (lambda d: d["fields"][0]["locate"][0].update(max_gap=-1), "distance"),
        (lambda d: d["fields"][0]["locate"][0].update(near={"labels": "oops"}), "list"),
        (lambda d: d.update(reference_image="../secret.jpg"), "relative path"),
        (lambda d: d.update(defaults={"items": []}), "unknown keys"),
        (
            lambda d: d["fields"][0].update(locate=[{"strategy": "regex", "pattern": "["}]),
            "invalid regex",
        ),
        (
            lambda d: d["fields"][0].update(
                locate=[{"strategy": "regex", "pattern": "x", "group": 1}]
            ),
            "group",
        ),
    ],
)
def test_validation_errors(mutate, match):
    data = template_data()
    mutate(data)
    with pytest.raises(ValueError, match=match):
        Template(data)


def test_discovery_override_copy_and_load(tmp_path, monkeypatch):
    registry = TemplateRegistry()
    assert len(registry.templates) == 5
    path = copy_builtin("tw_post_stamp_purchase", tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["name"] = "使用者覆寫"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("EZRECEIPT_TEMPLATES_DIR", str(tmp_path))
    overridden = TemplateRegistry().get(data["id"])
    assert overridden.user and overridden.name == "使用者覆寫"
    assert load_template(path.parent).id == data["id"]
    with pytest.raises(ValueError, match="already exists"):
        copy_builtin(data["id"], tmp_path)
    with pytest.raises(ValueError, match="Unknown built-in"):
        copy_builtin("missing", tmp_path)
    with pytest.raises(ValueError, match="Unknown template"):
        registry.get("missing")
    bad = tmp_path / "invalid.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid.json"):
        load_template(bad)


def test_traditional_and_fuzzy_matching():
    assert len(_TRADITIONAL) >= 150
    assert to_traditional("说明 统一编號 负责人 合计 数量 银貨 中華民国 免税 大学 区") == (
        "說明 統一編號 負責人 合計 數量 銀貨 中華民國 免稅 大學 區"
    )
    assert normalize_text("品　名 台") == "品 名 臺"
    assert to_traditional("台面干發") == "台面干發"
    assert has_label("統一捐號", ["統一編號"])
    assert has_label("合计新台带", ["合計新臺幣"])
    assert has_label("品　　名", ["品名"])
    assert not has_label("合十", ["合計"])


def test_keyword_ranking():
    a = Template(template_data())
    b_data = template_data()
    b_data["id"] = "second"
    b = Template(b_data, user=True)
    lines = [line("合成表單", 0.1, 0.1)]
    assert match_template([a, b], lines) is b
    b.user = False
    b.data["match"]["keywords_any"] = ["合成"]
    assert match_template([b, a], lines) is a
    a.data["match"]["keywords_all"] = ["必要文字"]
    assert match_template([a], lines) is None
    assert match_template([Template({**template_data(), "match": {}})], lines) is None


@pytest.mark.parametrize(
    "direction,value,position",
    [
        ("right", "右方", (0.55, 0.4)),
        ("left", "左方", (0.1, 0.4)),
        ("below", "下方", (0.4, 0.55)),
        ("above", "上方", (0.4, 0.2)),
        ("right_or_below", "下方", (0.4, 0.55)),
        ("left_or_right", "右方", (0.55, 0.4)),
        ("above_or_below", "下方", (0.4, 0.55)),
    ],
)
def test_anchor_directions(direction, value, position):
    lines = [line("測試標籤", 0.4, 0.4, 0.1), line(value, *position, width=0.1)]
    loc = {"strategy": "anchor", "labels": ["測試標籤"], "direction": direction}
    assert next(locate_values(lines, loc, "text")) == value


def test_anchor_near_not_near_skip_occurrence_and_same_line():
    lines = [
        line("總金額：120", 0.4, 0.1),
        line("買受人", 0.1, 0.1),
        line("總金額：360", 0.4, 0.7),
        line("專用章", 0.4, 0.64),
    ]
    locator = {"strategy": "anchor", "labels": ["總金額"], "direction": "right"}
    assert next(locate_values(lines, locator, "money")) == "120"
    assert next(locate_values(lines, {**locator, "occurrence": "last"}, "money")) == "360"
    near = {"labels": ["買受人"], "max_distance": 0.31}
    assert next(locate_values(lines, {**locator, "near": near}, "money")) == "120"
    assert next(locate_values(lines, {**locator, "not_near": near}, "money")) == "360"
    lines = [
        line("名稱", 0.4, 0.7),
        line("統一編號", 0.4, 0.64),
        line("12345678", 0.4, 0.58),
        line("合成商店", 0.4, 0.5),
    ]
    loc = {
        "strategy": "anchor",
        "labels": ["名稱"],
        "direction": "above",
        "skip": ["統一編號"],
        "skip_digits": True,
        "max_gap": 0.3,
    }
    assert next(locate_values(lines, loc, "text")) == "合成商店"
    loc["max_gap"] = 0.01
    assert list(locate_values(lines, loc, "text")) == []


def test_anchor_collect_band_and_stop_labels():
    lines = [
        line("統一編號", 0.6, 0.6, 0.25),
        line("福", 0.53, 0.54, 0.08),
        line("滿小", 0.63, 0.52, 0.12),
        line("吃", 0.79, 0.54, 0.08),
        line("另一行", 0.6, 0.3),
        line("12345678", 0.6, 0.65),
    ]
    loc = {
        "strategy": "anchor",
        "labels": ["統一編號"],
        "direction": "above",
        "collect": True,
        "skip_digits": True,
    }
    assert next(locate_values(lines, loc, "text")) == "福滿小吃"
    below = {**loc, "direction": "below", "skip_digits": False}
    assert next(locate_values(lines, below, "text")) == "12345678"
    lines = [line("茲證明：合成公司 本日確經購買 郵票", 0.1, 0.2, 0.8)]
    stop = {
        "strategy": "anchor",
        "labels": ["茲證明"],
        "direction": "right",
        "stop_labels": ["本日確經購買"],
    }
    assert next(locate_values(lines, stop, "text")) == "合成公司"
    assert normalize_value("( / )", "text") is None


def test_region_row_regex_and_normalizers():
    lines = [
        line("日期", 0.1, 0.2),
        line("109 年 10 月 15 日", 0.3, 0.2, 0.5),
        line("商品 2 50 100", 0.1, 0.4, 0.6),
        line("總計 100", 0.1, 0.8),
    ]
    region = {"strategy": "region", "box": [0, 0.35, 1, 0.5]}
    assert next(locate_values(lines, region, "items"))[0].amount == 100
    row = {
        "strategy": "row",
        "contains_all": ["年", "月"],
        "contains_any": ["日"],
        "within": [0, 0, 1, 0.5],
    }
    assert normalize_value(next(locate_values(lines, row, "date")), "date") == "2020-10-15"
    regex = {"strategy": "regex", "pattern": r"日期\s+(\d+ 年 \d+ 月 \d+ 日)", "group": 1}
    assert normalize_value(next(locate_values(lines, regex, "date")), "date") == "2020-10-15"
    assert normalize_value("12:30", "time") == "12:30:00"
    assert normalize_value("編號 01234567", "tax_id") == "01234567"
    assert normalize_value("2.5", "number") == 2.5


def test_fallback_defaults_and_cross_checks():
    data = template_data()
    data["defaults"] = {"merchant_name": "合成預設店", "tax": 0}
    data["fields"][0].update(cross_check="check_amount")
    data["fields"] += [
        {
            "key": "check_amount",
            "label": "核對",
            "type": "money",
            "target": "check",
            "locate": [{"strategy": "regex", "pattern": r"核對 (\d+)"}],
        },
        {
            "key": "item_name",
            "label": "品名",
            "type": "text",
            "target": "item_name",
            "default": "郵票",
            "locate": [{"strategy": "region", "box": [0.8, 0.8, 1, 1]}],
        },
    ]
    result = apply_template(
        Template(data),
        [line("合計：100", 0.1, 0.5), line("核對 200", 0.1, 0.6), line("日期 113/11/04", 0.1, 0.3)],
    )
    assert result.fields["total"] == 100 and result.fields["tax"] == 0
    assert result.fields["date"] == "2024-11-04"
    assert result.fields["merchant_name"] == "合成預設店"
    assert result.fields["items"][0].name == "郵票"
    assert result.report["item_name"] == "default"
    assert "check_amount" not in result.fields
    assert "cross_check_mismatch:total" in result.warnings
    assert "template_default_used:item_name" in result.warnings


def synthetic_form():
    return [
        line("免用統一發票收據", 0.1, 0.02, 0.5),
        line("統一編號", 0.55, 0.08),
        line("11223344", 0.8, 0.08),
        line("合成買方", 0.1, 0.13),
        line("台照", 0.3, 0.13),
        line("109 年 10 月 15 日", 0.5, 0.16, 0.45),
        line("品 名", 0.12, 0.23),
        line("數量", 0.35, 0.23, 0.08),
        line("單價", 0.45, 0.23, 0.08),
        line("總價", 0.57, 0.23, 0.08),
        line("備註", 0.8, 0.23, 0.1),
        line("1", 0.03, 0.33, 0.02),
        line("合成餐盒", 0.12, 0.33),
        line("2", 0.35, 0.33, 0.08),
        line("290", 0.45, 0.33, 0.08),
        line("580", 0.57, 0.33, 0.08),
        line("合成小店", 0.77, 0.33),
        line("統一編號", 0.77, 0.4),
        line("55667788", 0.77, 0.46),
        line("／／／", 0.12, 0.55),
        line("合計新臺幣 ✗萬 ✗仟 伍 佰 捌 拾 ✗元", 0.05, 0.8, 0.85),
    ]


@pytest.mark.parametrize(
    "grid,total", [("✗萬 ✗仟 伍 佰 捌 拾 ✗元", 580), ("— 萬 — 仟 玖 百 陸 拾 元", 960)]
)
def test_synthetic_no_invoice_table_and_grid(grid, total):
    lines = synthetic_form()
    lines[-1] = line("合計新臺幣 " + grid, 0.05, 0.8, 0.85)
    result = apply_template(TemplateRegistry().get("tw_no_uniform_invoice_receipt"), lines)
    assert result.fields["buyer_tax_id"] == "11223344"
    assert result.fields["merchant_tax_id"] == "55667788"
    assert result.fields["merchant_name"] == "合成小店"
    assert result.fields["date"] == "2020-10-15"
    assert result.fields["total"] == total
    assert len(result.fields["items"]) == 1
    assert result.fields["items"][0].name == "合成餐盒"
    assert result.fields["items"][0].quantity == 2


def textured_reference():
    rng = np.random.default_rng(42)
    image = np.full((600, 900, 3), 240, np.uint8)
    for _ in range(350):
        x, y = rng.integers([20, 20], [880, 580])
        cv2.circle(
            image,
            (int(x), int(y)),
            int(rng.integers(2, 9)),
            tuple(int(c) for c in rng.integers(0, 200, 3)),
            -1,
        )
    cv2.putText(image, "SYNTHETIC 580", (200, 300), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3)
    return image


def test_orb_recovers_transform_and_pipeline_regions(tmp_path, validate_payload):
    reference = textured_reference()
    transform = np.float64([[0.93, -0.04, 35], [0.02, 0.92, 20], [0.00002, -0.00002, 1]])
    captured = cv2.warpPerspective(reference, transform, (900, 600))
    result = align_to_reference(captured, reference)
    assert result is not None and result[2] >= 25
    points = np.float32([[[200, 200], [700, 450]]])
    projected = cv2.perspectiveTransform(cv2.perspectiveTransform(points, transform), result[1])
    assert np.max(np.abs(projected - points)) < 3
    assert align_to_reference(np.zeros_like(reference), reference) is None
    data = template_data()
    data["reference_image"] = "reference.jpg"
    data["fields"][0]["locate"] = [{"strategy": "region", "box": [0.2, 0.4, 0.8, 0.6]}]
    path = tmp_path / "synthetic"
    path.mkdir()
    cv2.imencode(".jpg", reference)[1].tofile(path / "reference.jpg")
    (path / "template.json").write_text(json.dumps(data), encoding="utf-8")
    registry = TemplateRegistry(tmp_path)
    alignment = match_reference(registry.templates, captured)
    assert alignment.template.id == "synthetic"
    calls = []

    def recognize(image):
        calls.append(image)
        assert image.shape == reference.shape
        return [line("580", 0.4, 0.45)]

    receipt = Pipeline("ocr", SimpleNamespace(recognize=recognize), registry).process(captured)
    assert receipt.template_id == "synthetic" and receipt.total == 580
    assert len(calls) == 1
    validate_payload(receipt)


def test_force_and_disable_templates():
    image = np.full((1200, 800, 3), 255, np.uint8)
    engine = SimpleNamespace(recognize=lambda _: [line("總計 100", 0.1, 0.8)])
    forced = Pipeline("ocr", engine, template="tw_post_stamp_purchase").process(image)
    assert forced.template_id == "tw_post_stamp_purchase" and forced.total == 100
    assert "template_default_used:item_name" in forced.warnings
    plain = Pipeline("ocr", engine, use_templates=False).process(image)
    assert plain.template_id is None and plain.total == 100
    with pytest.raises(ValueError, match="Unknown template"):
        Pipeline(template="missing")
    with pytest.raises(ValueError, match="mutually exclusive"):
        Pipeline(template="tw_post_stamp_purchase", use_templates=False)


def test_table_computes_amount_and_falls_back_between_strategies():
    data = template_data()
    data["fields"][0]["locate"] = [
        {"strategy": "region", "box": [0.8, 0.8, 1, 1]},
        {"strategy": "anchor", "labels": ["合計"], "direction": "right"},
    ]
    result = apply_template(Template(data), [line("合計：100", 0.1, 0.6)])
    assert result.fields["total"] == 100 and result.report["total"] == "anchor"
    lines = synthetic_form()
    lines = [entry for entry in lines if entry.text != "580"]
    result = apply_template(TemplateRegistry().get("tw_no_uniform_invoice_receipt"), lines)
    assert len(result.fields["items"]) == 1
    assert result.fields["items"][0].amount == 580


def test_table_fuzzy_long_label():
    lines = [
        line("商品名稻", 0.1, 0.2, 0.3),
        line("金額", 0.6, 0.2),
        line("合成商品", 0.1, 0.3, 0.3),
        line("100", 0.6, 0.3),
    ]
    locator = {"strategy": "table", "columns": {"name": ["商品名稱"], "amount": ["金額"]}}
    result = parse_table(lines, locator)
    assert len(result) == 1 and result[0].name == "合成商品" and result[0].amount == 100


@pytest.mark.parametrize("header", ["品名", "商品名稱", "商品名稻"])
def test_table_skips_person_row_before_full_name_header(header):
    locator = {
        "strategy": "table",
        "columns": {"name": ["品名", "商品名稱"], "amount": ["價格"]},
    }
    misleading = [
        line("姓名", 0.05, 0.1, 0.1),
        line("王小明", 0.2, 0.1, 0.3),
        line("價格表", 0.7, 0.1),
    ]
    item = [line("合成商品", 0.2, 0.3, 0.3), line("100", 0.7, 0.3)]
    assert parse_table(misleading + item, locator) == []
    lines = misleading + [line(header, 0.2, 0.2, 0.3), line("價格", 0.7, 0.2)] + item
    result = parse_table(lines, locator)
    assert len(result) == 1
    assert result[0].name == "合成商品" and result[0].amount == 100
