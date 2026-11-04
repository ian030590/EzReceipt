from types import SimpleNamespace

import numpy as np
import pytest

from ezreceipt.extractors.ocr import OcrEngine, OcrLine, parse_generic
from ezreceipt.normalize import parse_date, parse_date_detail
from ezreceipt.pipeline import Pipeline
from ezreceipt.templates.engine import apply_template
from ezreceipt.templates.model import Template
from ezreceipt.templates.registry import TemplateRegistry

from .synthetic import row_lines
from .test_phase4 import fixture_lines
from .test_templates import template_data


@pytest.mark.parametrize("pattern", ["[", None, 123, []])
def test_reject_pattern_is_validated(pattern):
    data = template_data()
    data["fields"][0]["reject_pattern"] = pattern
    with pytest.raises(ValueError, match="reject_pattern: invalid regex"):
        Template(data)


@pytest.mark.parametrize("fallback", ["candidate", "locator", "default"])
def test_rejected_value_tries_next_candidate_locator_or_default(fallback):
    data = template_data()
    data["generic_fallback"] = False
    data["fields"][0].update(
        type="text", target="merchant_name", reject_pattern="地址", default="預設商家"
    )
    lines = [
        OcrLine("合計", (0.02, 0.2, 0.12, 0.25)),
        OcrLine("地址：範例路3號", (0.15, 0.2, 0.4, 0.25)),
    ]
    if fallback == "candidate":
        lines.append(OcrLine("範例商家", (0.42, 0.2, 0.6, 0.25)))
    elif fallback == "locator":
        lines.append(OcrLine("範例商家", (0.1, 0.6, 0.4, 0.65)))
        data["fields"][0]["locate"].append(
            {"strategy": "region", "box": [0, 0.5, 1, 0.7]}
        )
    result = apply_template(Template(data), lines)
    assert result.fields["merchant_name"] == (
        "預設商家" if fallback == "default" else "範例商家"
    )
    assert result.report["total"] == {
        "candidate": "anchor", "locator": "region", "default": "default"
    }[fallback]


def test_empty_reject_pattern_discards_all_located_values():
    data = template_data()
    data["fields"][0].update(reject_pattern="", default=600)
    result = apply_template(Template(data), row_lines(["合計 300"]))
    assert result.fields["total"] == 600
    assert result.report["total"] == "default"


@pytest.mark.parametrize("field", ["payer", "merchant_name", "item_name"])
@pytest.mark.parametrize(
    "sensitive",
    [
        "同上", "相異", "地址", "戶籍", "通訊", "電話", "身分證",
        "C223456789", "0934567890", "02-23456789", "0223456789",
        "037123456", "037-12345678", "臺北市範例街3巷5號",
    ],
)
def test_payment_fields_reject_sensitive_values(field, sensitive):
    template = TemplateRegistry().get("tw_payment_receipt")
    spec = next(spec for spec in template.fields if spec["key"] == field)
    spec["locate"] = [{"strategy": "region", "box": [0, 0, 1, 1]}]
    template.data["fields"] = [spec]
    result = apply_template(template, row_lines([sensitive]))
    if field == "payer":
        assert result.fields["custom_fields"][0].value is None
    elif field == "merchant_name":
        assert result.fields.get("merchant_name") is None
    else:
        assert result.fields["items"][0].name == "領款"


@pytest.mark.parametrize("use_cls", [False, True])
def test_ocr_forwards_angle_classifier_setting(use_cls):
    calls = []
    image = np.zeros((10, 10, 3), np.uint8)

    def engine(image, *, use_cls):
        calls.append(use_cls)

    wrapper = OcrEngine(engine) if not use_cls else OcrEngine(engine, use_cls=True)
    assert wrapper.use_cls is use_cls
    assert wrapper.recognize(image) == []
    assert calls == [use_cls]


def test_ocr_does_not_retry_internal_type_errors():
    calls = []

    def engine(image, *, use_cls):
        calls.append(use_cls)
        raise TypeError("Synthetic internal engine failure")

    with pytest.raises(TypeError, match="internal engine failure"):
        OcrEngine(engine).recognize(np.zeros((10, 10, 3), np.uint8))
    assert calls == [False]


@pytest.mark.parametrize("year", [14, 15, 16])
def test_truncated_roc_year_is_inferred_only_in_current_window(year, fixed_today):
    text = f"{year}年6月30日"
    expected = f"{year + 2011}-06-30"
    assert parse_date_detail(text) == (expected, ["date_year_inferred"])
    assert parse_date(text) == expected
    assert parse_date_detail(f"{text} 15:44") == (expected, ["date_year_inferred"])


@pytest.mark.parametrize(
    "text",
    [
        "13年6月30日", "17年6月30日", "05年6月30日", "59年6月30日",
        "015年6月30日", "15年6月30", "15.06.30", "15-06-30", "15/06/30",
        "150630", "15年2月30日", "15年13月1日",
    ],
)
def test_invalid_or_other_formats_do_not_infer_a_roc_year(text, fixed_today):
    assert parse_date_detail(text) == (None, [])


@pytest.mark.parametrize("text", ["115年6月30日", "2026-06-30", "15年2月30日 115年6月30日"])
def test_explicit_valid_dates_do_not_warn_about_inference(text, fixed_today):
    assert parse_date_detail(text) == ("2026-06-30", [])


@pytest.mark.parametrize("split_date", [False, True])
def test_generic_parser_reports_inferred_date_once(split_date, fixed_today):
    date_row = "15年 | 6月30日" if split_date else "15年6月30日"
    result = parse_generic(row_lines([date_row, "總計 600"]))
    assert result.fields["date"] == "2026-06-30"
    assert result.warnings == ["date_year_inferred"]


def test_template_date_inference_reaches_payload(fixed_today):
    lines = fixture_lines("payment_receipt_handwritten")
    result = Pipeline("ocr", SimpleNamespace(recognize=lambda image: lines)).process(
        np.full((1200, 800, 3), 255, np.uint8)
    )
    assert result.date == "2026-06-30"
    assert result.warnings == ["date_year_inferred"]
    assert result.to_payload()["warnings"] == ["date_year_inferred"]


@pytest.mark.parametrize("explicit_date", [None, "115年6月29日"])
def test_generic_date_warning_survives_template_fallback_only_when_used(explicit_date, fixed_today):
    data = template_data()
    if explicit_date:
        data["fields"].append({
            "key": "date", "label": "日期", "type": "date", "target": "date",
            "default": explicit_date,
            "locate": [{"strategy": "regex", "pattern": "(missing)"}],
        })
    result = apply_template(Template(data), row_lines(["15年6月30日", "合計 600"]))
    assert result.fields["date"] == ("2026-06-29" if explicit_date else "2026-06-30")
    assert ("date_year_inferred" in result.warnings) is (explicit_date is None)


@pytest.mark.parametrize(
    "rows,expected,payment_warning",
    [
        (["總計： | 900", "1项 | 800", "應收金額： | 158", "現金 | 1000"], 900, False),
        (["總計：", "1项 | 800", "應收金額： | 158", "現金 | 1000"], 800, False),
        (["1项", "2项 | 800", "應收金額： | 158", "現金 | 1000"], 800, False),
        (["總計：", "應收金額： | 158", "現金 | 1000"], 158, False),
        (["總計：", "銷售總額： | 1项 | 683", "現金 | 1000"], 683, False),
        (["總計：", "應收金額：", "銷售總額： | 683", "現金 | 1000"], 683, False),
        (["總計：", "應收金額：", "現金 | 1000"], 1000, True),
    ],
)
def test_cash_register_total_fallback_order(rows, expected, payment_warning):
    result = apply_template(TemplateRegistry().get("tw_cash_register_invoice"), row_lines(rows))
    assert result.fields["total"] == expected
    assert ("total_from_payment" in result.warnings) is payment_warning


@pytest.mark.parametrize("text", ["115-10-07 13:26", "2026-04-06 13:19#"])
def test_cash_register_separate_datetime_row(text, fixed_today):
    lines = [
        OcrLine("日期：", (0.05, 0.2, 0.3, 0.24)),
        OcrLine(text, (0.05, 0.3, 0.8, 0.34)),
    ]
    result = apply_template(TemplateRegistry().get("tw_cash_register_invoice"), lines)
    assert result.fields["date"] == parse_date(text)
    assert result.fields["time"] == ("13:26:00" if text.startswith("115") else "13:19:00")


@pytest.mark.parametrize("first_has_time", [False, True])
def test_cash_register_time_comes_from_first_valid_date_row(first_has_time, fixed_today):
    first = "115-10-07 13:26" if first_has_time else "115-10-07"
    lines = [
        OcrLine("日期： 12:00", (0.05, 0.1, 0.8, 0.14)),
        OcrLine("115-02-30 12:31", (0.05, 0.2, 0.8, 0.24)),
        OcrLine(first, (0.05, 0.3, 0.8, 0.34)),
        OcrLine("115-10-08 14:20", (0.05, 0.4, 0.8, 0.44)),
    ]
    result = apply_template(TemplateRegistry().get("tw_cash_register_invoice"), lines)
    assert result.fields["date"] == "2026-10-07"
    assert result.fields.get("time") == ("13:26:00" if first_has_time else None)


def test_cash_register_date_fallback_stays_in_top_45_percent(fixed_today):
    lines = [OcrLine("115-10-07 13:26", (0.05, 0.46, 0.8, 0.50))]
    result = apply_template(TemplateRegistry().get("tw_cash_register_invoice"), lines)
    assert result.fields.get("date") is None
    assert result.fields.get("time") is None


def test_row_date_required_option_requires_boolean():
    data = template_data()
    data["fields"][0]["locate"] = [{"strategy": "row", "date_required": "true"}]
    with pytest.raises(ValueError, match="date_required: expected boolean"):
        Template(data)
