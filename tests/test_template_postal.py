import pytest

from ezreceipt.templates.engine import apply_template
from ezreceipt.templates.registry import TemplateRegistry

from .test_templates import line


@pytest.mark.parametrize(
    "chinese,expected_warning",
    [
        ("壹萬元整", False),
        ("貳萬元整", True),
    ],
)
def test_synthetic_postal_layout(chinese, expected_warning):
    lines = [
        line("購買票品證明單", 0.35, 0.1, 0.25),
        line("第", 0.76, 0.11, 0.03),
        line("001234號", 0.8, 0.11),
        line("茲證明", 0.06, 0.26, 0.08),
        line("合成買方", 0.22, 0.26, 0.2),
        line("本日確經購買", 0.54, 0.26),
        line("郵票", 0.77, 0.26, 0.08),
        line("統一編號", 0.07, 0.31, 0.1),
        line("11223344", 0.2, 0.31),
        line("計新臺幣", 0.07, 0.37, 0.13),
        line(chinese, 0.22, 0.37),
        line("合成郵局 123 A", 0.77, 0.32, 0.12),
        line("(NT:", 0.11, 0.45, 0.09),
        line("10,000.00", 0.24, 0.45, 0.2),
        line("104.05.06-13", 0.76, 0.42, 0.15),
    ]
    result = apply_template(TemplateRegistry().get("tw_post_stamp_purchase"), lines)
    fields = result.fields
    assert fields["date"] == "2015-05-06" and fields["total"] == 10000
    assert fields["buyer_tax_id"] == "11223344" and fields["tax"] == 0
    assert fields["items"][0].name == "郵票" and fields["items"][0].quantity == 1
    custom = {f.key: f.value for f in fields["custom_fields"]}
    assert custom == {"serial_no": "001234", "buyer": "合成買方", "post_office": "合成郵局"}
    assert ("cross_check_mismatch:total" in result.warnings) == expected_warning
