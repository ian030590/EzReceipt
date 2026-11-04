import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from ezreceipt.cli import main
from ezreceipt.extractors.ocr import OcrLine, parse_generic
from ezreceipt.imaging import ocr_input
from ezreceipt.normalize import parse_date, parse_time
from ezreceipt.pipeline import Pipeline, validate_totals
from ezreceipt.templates.engine import apply_template
from ezreceipt.templates.matcher import has_label, match_template
from ezreceipt.templates.model import Template
from ezreceipt.templates.registry import TemplateRegistry

from .synthetic import row_lines
from .test_templates import template_data


def fixture_lines(name):
    path = Path(__file__).parent / "fixtures" / f"synthetic_{name}.json"
    return [OcrLine(**line) for line in json.loads(path.read_text(encoding="utf-8"))["lines"]]


@pytest.mark.parametrize(
    "text",
    [
        "115年01-02月",
        "115年1-2月份",
        "中華民國115年1-2月份",
        "115/01-02",
        "115.01/02",
        "115-01.02",
        "115年01-02月",
        "115-01月02日",
    ],
)
def test_periods_and_mixed_separators_are_not_dates(text):
    assert parse_date(text) is None


@pytest.mark.parametrize(
    "text,expected_date,expected_time",
    [
        ("2026-02-1115:59:34", "2026-02-11", "15:59:34"),
        ("乘車日期:2026-10-1306:30:00", "2026-10-13", "06:30:00"),
    ],
)
def test_merged_datetime(text, expected_date, expected_time):
    assert parse_date(text) == expected_date
    assert parse_time(text) == expected_time


@pytest.mark.parametrize("separator", ["-", "/"])
def test_two_digit_western_year_requires_context_and_current_bound(separator):
    limit = date.today().year % 100 + 1
    text = f"26{separator}02{separator}03"
    assert parse_date(text) is None
    assert parse_date(f"{text} 15:44") == "2026-02-03"
    assert parse_date(f"日期：{text}") == "2026-02-03"
    assert parse_date(f"date: {text}") == "2026-02-03"
    assert parse_date(f"日期：{limit:02}{separator}02{separator}03") == f"20{limit:02}-02-03"
    assert parse_date(f"日期：{limit + 1:02}{separator}02{separator}03 15:44") is None
    assert parse_date("26.02.03 15:44") is None
    assert parse_date("60年1月1日") == "1971-01-01"


def test_ocr_input_recovers_coloured_dots_without_changing_source():
    image = np.full((60, 80, 3), 255, np.uint8)
    image[20:40:3, 20:60:3] = (230, 230, 40)
    original = image.copy()
    result = ocr_input(image)
    expected = cv2.GaussianBlur(image.min(axis=2), (0, 0), 1.0)
    assert result.shape == image.shape and result.dtype == np.uint8
    assert np.array_equal(image, original)
    assert np.array_equal(result[:, :, 0], expected)
    assert np.array_equal(result[:, :, 0], result[:, :, 1])
    assert np.array_equal(result[:, :, 1], result[:, :, 2])
    assert 40 < int(result[23, 23, 0]) < 255
    gray_blurred = cv2.GaussianBlur(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (0, 0), 1.0)
    assert result[23, 23, 0] < gray_blurred[23, 23]
    assert ocr_input(np.full((10, 10), 123, np.uint8)).shape == (10, 10, 3)


def test_pipeline_transforms_only_the_ocr_input(monkeypatch):
    source = np.full((1200, 800, 3), (220, 70, 180), np.uint8)
    aligned = source.copy()
    seen = []

    def qr(image):
        assert image is source
        seen.append("qr")

    def align(candidates, image):
        assert image is source
        seen.append("align")

    def recognize(image):
        assert np.array_equal(image, ocr_input(aligned))
        seen.append("ocr")
        return row_lines(["總計 252"])

    def hook(lines, image):
        assert image is source
        seen.append("template")
        return parse_generic(lines)

    monkeypatch.setattr("ezreceipt.pipeline.einvoice.extract", qr)
    monkeypatch.setattr("ezreceipt.pipeline.preprocess_ocr", lambda image: image)
    monkeypatch.setattr("ezreceipt.pipeline.match_reference", align)
    result = Pipeline(
        ocr_engine=SimpleNamespace(recognize=recognize), templates=SimpleNamespace(extract=hook)
    ).process(source)
    assert result.total == 252
    assert seen == ["qr", "align", "ocr", "template"]


@pytest.mark.parametrize("key", ["keywords_any", "keywords_all"])
def test_short_keywords_are_exact_and_long_keywords_allow_one_substitution(key):
    data = template_data()
    data["match"] = {key: ["票品證明"]}
    template = Template(data)
    assert match_template([template], row_lines(["票務證明章"])) is None
    assert has_label("票務證明章", ["票品證明"])
    template.data["match"][key] = ["購買票品證明單"]
    assert match_template([template], row_lines(["購買票務證明單"])) is template


def test_keyword_exclusions_are_exact_and_normalize_simplified_characters():
    data = template_data()
    data["match"] = {
        "keywords_all": ["發票", "銀", "機", "統", "項", "現"],
        "keywords_none": ["高鐵", "車票", "票品證明"],
    }
    template = Template(data)
    assert match_template([template], row_lines(["发票 银 机 统 项 现 票務證明"])) is template
    assert match_template([template], row_lines(["发票 银 机 统 项 现 车票"])) is None
    post = TemplateRegistry().get("tw_post_stamp_purchase")
    assert match_template([post], row_lines(["購買票品證明單", "高鐵"])) is None


@pytest.mark.parametrize(
    "row,key,expected",
    [
        ("總計： | 1项 | 252", "total", 252),
        ("總計： 1项 252", "total", 252),
        ("總計： 1 pcs 252", "total", 252),
        ("總計： 1件 252", "total", 252),
        ("總計： 1個 252", "total", 252),
        ("營業稅： 5.000% 12", "tax", 12),
        ("營業稅額： | 5.000% | 12", "tax", 12),
        ("銷售額合計 | 240", "subtotal", 240),
        ("銷售額： | 240", "subtotal", 240),
    ],
)
def test_labelled_amounts_ignore_quantities_and_percentages(row, key, expected):
    assert parse_generic(row_lines([row])).fields[key] == expected


@pytest.mark.parametrize("mark", ["NT$", "NT", "新台幣", "新臺幣"])
def test_currency_total_requires_one_distinct_amount(mark):
    result = parse_generic(row_lines([f"{mark} 870 現金", f"{mark} 870"]))
    assert result.fields["total"] == 870
    assert "total_from_currency_mark" in result.warnings
    ambiguous = parse_generic(row_lines([f"{mark} 870", f"{mark} 1490"]))
    assert ambiguous.fields.get("total") is None
    assert "total_from_currency_mark" not in ambiguous.warnings
    labelled = parse_generic(row_lines([f"{mark} 870", "總計 1490"]))
    assert labelled.fields["total"] == 1490
    assert "total_from_currency_mark" not in labelled.warnings


@pytest.mark.parametrize("buyer_label", ["買方", "買受人"])
def test_explicit_buyer_and_seller_override_unlabelled_tax_ids(buyer_label):
    result = parse_generic(
        row_lines(
            [
                "統编：12345675",
                "賣方34567890",
                f"{buyer_label} | 12345675",
                "統一編號 12345675",
            ]
        )
    )
    assert result.fields["merchant_tax_id"] == "34567890"
    assert result.fields["buyer_tax_id"] == "12345675"
    result = parse_generic(row_lines([f"{buyer_label} 12345675", "統編12345675"]))
    assert result.fields.get("merchant_tax_id") is None


@pytest.mark.parametrize(
    "title",
    [
        "收銀機統一發票（三聯副聯式扣抵聯）",
        "電子發票證明聯",
        "領款收據",
        "票務證明章",
        "格式25",
        "隨機碼 1234",
        "www.example.com.tw",
        "http://example.com",
        "TEL:0223456789",
        "電話:0223456789",
        "統編12345675",
        "統一編號12345675",
        "地址範例路1號",
        "格",
        "式：25",
    ],
)
def test_merchant_candidates_exclude_titles_and_metadata(title):
    lines = [OcrLine(title, (0.1, 0.02, 0.9, 0.04)), OcrLine("範例電子", (0.1, 0.1, 0.9, 0.13))]
    assert parse_generic(lines).fields["merchant_name"] == "範例電子"


def test_a4_merchant_never_contains_format_fragments():
    name = parse_generic(fixture_lines("einvoice_a4")).fields.get("merchant_name")
    assert not name or "格式" not in name and "式" not in name


@pytest.mark.parametrize("flag", ["TX", "T", "N"])
def test_wrapped_item_amount_can_occupy_its_own_row(flag):
    result = parse_generic(row_lines(["1 * @ 8", "電池盒3只", f"8 {flag}", "總計 8"]))
    assert len(result.fields["items"]) == 1
    item = result.fields["items"][0]
    assert (item.name, item.quantity, item.unit_price, item.amount) == ("電池盒3只", 1, 8, 8)


def test_missing_payment_optionals_are_quiet_and_missing_date_is_not_guessed():
    lines = fixture_lines("payment_receipt_typed")
    lines = [line for line in lines if line.box[0] != 0.7]
    lines.append(OcrLine("115年9月5日", (0.6, 0.8, 0.9, 0.83)))
    template = TemplateRegistry().get("tw_payment_receipt")
    result = apply_template(template, lines)
    assert result.fields.get("date") is None
    assert "template_field_missing:date" in result.warnings
    assert all(
        f"template_field_missing:{key}" not in result.warnings
        for key in (
            "withholding_tax",
            "nhi_premium",
            "net_amount",
        )
    )


def test_payment_default_item_and_privacy_without_a_recipient():
    lines = fixture_lines("payment_receipt_typed")
    lines = [
        line
        for line in lines
        if line.text not in ("王小明", "用途說明", "115年9月王小明電腦處理費")
    ]
    result = Pipeline("ocr", SimpleNamespace(recognize=lambda image: lines)).process(
        np.full((1200, 800, 3), 255, np.uint8)
    )
    payload = json.dumps(result.to_payload(), ensure_ascii=False)
    assert all(text not in payload for text in ("A123456789", "0912345678", "範例路1號"))
    assert result.items[0].name == "領款" and result.items[0].amount == 3000
    assert "template_default_used:item_name" in result.warnings


def test_cash_register_payment_fallback_and_first_printed_amounts():
    lines = fixture_lines("cash_register_1")
    total_y = next(line.box[1] for line in lines if line.text == "計：")
    lines = [line for line in lines if line.box[1] != total_y]
    template = TemplateRegistry().get("tw_cash_register_invoice")
    result = apply_template(template, lines)
    assert result.fields["total"] == 252
    assert "total_from_payment" in result.warnings
    assert "template_field_missing:total" not in result.warnings
    assert result.fields["subtotal"] == 240 and result.fields["tax"] == 12


def test_thsr_missing_ride_date_does_not_use_print_or_payment_date():
    lines = [
        line
        for line in fixture_lines("thsr_proof")
        if not any(label in line.text for label in ("乘車日期", "控管編號", "票證類別"))
    ]
    result = apply_template(TemplateRegistry().get("tw_thsr_ticket_proof"), lines)
    assert result.fields.get("date") is None and result.fields.get("time") is None
    assert "template_field_missing:date" in result.warnings
    assert "template_field_missing:control_no" not in result.warnings
    assert "template_field_missing:ticket_type" not in result.warnings


def test_item_total_can_be_tax_exclusive_but_real_mismatches_still_warn():
    result = apply_template(
        TemplateRegistry().get("tw_cash_register_invoice"), fixture_lines("cash_register_1")
    )
    warnings = []
    validate_totals(result.fields, warnings)
    assert warnings == []
    result.fields["tax"] = 5
    validate_totals(result.fields, warnings)
    assert warnings == ["items_total_mismatch"]


def test_new_templates_are_listed_by_cli(capsys):
    assert main(["template", "list"]) == 0
    output = capsys.readouterr().out
    assert all(
        name in output
        for name in (
            "tw_payment_receipt",
            "tw_cash_register_invoice",
            "tw_thsr_ticket_proof",
        )
    )


@pytest.mark.parametrize(
    "change",
    [
        {"generic_fallback": "false"},
        {"match": {"keywords_none": "高鐵"}},
    ],
)
def test_new_template_options_are_validated(change):
    with pytest.raises(ValueError):
        Template({**template_data(), **change})


def test_optional_requires_boolean():
    data = template_data()
    data["fields"][0]["optional"] = "true"
    with pytest.raises(ValueError, match="optional"):
        Template(data)
