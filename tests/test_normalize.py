from datetime import date

import pytest

from ezreceipt.normalize import (
    normalize_text,
    parse_date,
    parse_money,
    parse_tax_id,
    parse_time,
)


@pytest.mark.parametrize(
    "text",
    [
        "2024-11-04",
        "2024/11/04",
        "2024.11.04",
        "20241104",
        "2024年11月4日",
        "113/11/04",
        "113-11-04",
        "113.11.04",
        "1131104",
        "113年11月4日",
        "民國113年11月4日",
        "中華民國 113 年 11 月 04 日",
        "日期： 2O24 / 11 / O4",
    ],
)
def test_date(text):
    assert parse_date(text) == "2024-11-04"


@pytest.mark.parametrize("char", ["(", "（", "|", "l", "I", "!", "]", "】"])
@pytest.mark.parametrize("space", ["", "  "])
def test_date_ocr_one_after_year_or_month(char, space):
    assert parse_date(f"中華民国109年{space}{char}2月5日") == "2020-12-05"
    assert parse_date(f"113年{space}{char}1月4日") == "2024-11-04"
    assert parse_date(f"113年11月{space}{char}4日") == "2024-11-14"


@pytest.mark.parametrize("char", ["(", "（", "|", "!", "]", "】"])
def test_date_ocr_one_requires_year_or_month(char):
    assert parse_date(f"109/{char}2/5") is None
    assert parse_date(f"113/11/{char}4") is None
    assert parse_date(f"商品{char}2號") is None
    assert parse_date(f"113年{char} 1月4日") is None


@pytest.mark.parametrize("text", ["2024/02/30", "113/13/01", "000/01/01", "no date"])
def test_invalid_date(text):
    assert parse_date(text) is None


def test_roc_bounds():
    year = date.today().year - 1911
    assert parse_date(f"{year + 1}/01/01") is not None
    assert parse_date(f"{year + 2}/01/01") is None
    assert parse_date("1年1月1日") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("NT$ 1,234.50 元", 1234.5),
        ("TWD -35", -35),
        ("１，２３４．５０", 1234.5),
        ("新臺幣壹萬貳仟參佰元整", 12300),
        ("一千二百五十元", 1250),
        ("1 萬 2 仟 3 佰 4 拾 5 元", 12345),
        ("壹 萬 零 仟 伍 佰 零 拾 零 元", 10500),
        ("十二元", 12),
        ("一億兩千萬元", 120000000),
        ("〇元", 0),
        ("1O0", 100),
        ("I2l", 121),
        ("coffee", None),
        ("", None),
    ],
)
def test_money(text, expected):
    assert parse_money(text) == expected


def test_time_tax_text():
    assert parse_time("時間 12:30") == "12:30:00"
    assert parse_time("O9:O5:12") == "09:05:12"
    assert parse_time("25:10") is None
    assert parse_time("12:80:30") is None
    assert parse_tax_id("統編：01234567") == "01234567"
    assert parse_tax_id("00000000") is None
    assert parse_tax_id("123456789") is None
    assert normalize_text("  台灣ＡＢＣ  ") == "臺灣ABC"
