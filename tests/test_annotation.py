import numpy as np
import pytest

from ezreceipt.templates.annotate import (
    Annotation,
    build_template,
    canvas_to_normalized,
    normalized_to_canvas,
    read_image,
    remove_annotation,
    save_template,
    slugify,
    suggest_keywords,
)
from ezreceipt.templates.model import load_template

from .test_templates import line


@pytest.mark.parametrize(
    "sample_text",
    [
        "009214號",
        "1A2B3C4號5",
        "交易序號AB1234CD",
        "日期 113年11月4日",
        "日期 113/1/4",
        "總金額 NT$ 50",
        "應付 $50",
        "金額 TWD 50",
        "郵資 NT: 50",
        "金額 50元",
        "金額 壹佰元整",
        "合計 50",
        "USD 12.50",
    ],
)
def test_keywords_skip_sample_specific_text(sample_text):
    lines = [line(sample_text, 0.1, 0.01), line("購買票品證明單", 0.1, 0.1)]
    assert suggest_keywords(lines) == ["購買票品證明單"]


def test_keywords_keep_titles_order_and_limit():
    lines = [
        line("合成郵局", 0.1, 0.1),
        line("合成郵局", 0.1, 0.12),
        line("合成表單", 0.1, 0.03),
        line("表單第2版", 0.1, 0.18),
        line("頁尾", 0.1, 0.8),
        line("第四標題", 0.1, 0.2),
    ]
    assert suggest_keywords(lines) == ["合成表單", "合成郵局", "表單第2版"]


def test_geometry_slug_and_delete():
    box = canvas_to_normalized((800, 400, -5, 100), (1000, 500))
    assert box == (0, 0.2, 0.8, 0.8)
    assert normalized_to_canvas(box, (1000, 500)) == (0, 100, 800, 400)
    assert slugify("Serial No.") == "serial_no"
    assert slugify("交易序號").startswith("field_")
    assert slugify("交易序號") == slugify("交易序號")
    for coords, size in [((0, 0, 0, 1), (100, 100)), ((0, 0, 1, 1), (0, 100))]:
        with pytest.raises(ValueError):
            canvas_to_normalized(coords, size)
    annotation = Annotation("total", "總金額", "total", "money", (0.4, 0.7, 0.8, 0.8))
    assert remove_annotation([annotation], 0) == []
    with pytest.raises(ValueError):
        remove_annotation([], 0)


def test_build_save_load_roundtrip(tmp_path):
    lines = [line("合成表單", 0.1, 0.05), line("合計", 0.1, 0.7), line("580", 0.4, 0.7)]
    annotations = [Annotation("total", "金額", "total", "money", (0.4, 0.69, 0.8, 0.75))]
    keywords = suggest_keywords(lines)
    assert keywords == ["合成表單"]
    template = build_template("synthetic", "合成模板", annotations, keywords, lines)
    assert [s["strategy"] for s in template.fields[0]["locate"]] == ["region", "anchor"]
    image = np.full((300, 500, 3), 255, np.uint8)
    path = save_template(template, tmp_path, image)
    loaded = load_template(path)
    assert loaded.data == template.data
    assert loaded.reference_path == path.parent / "reference.jpg"
    assert read_image(loaded.reference_path).shape == image.shape
    with pytest.raises(ValueError, match="already exists"):
        save_template(template, tmp_path, image)
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"invalid")
    with pytest.raises(ValueError, match="Cannot read"):
        read_image(bad)
