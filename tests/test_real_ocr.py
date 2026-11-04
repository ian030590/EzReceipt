from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from ezreceipt import scan_image

FONT = Path("C:/Windows/Fonts/msjh.ttc")


@pytest.mark.skipif(not FONT.exists(), reason="Microsoft JhengHei font is unavailable")
def test_offline_real_ocr(validate_payload):
    image = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT), 44)
    for text, y in [
        ("合成測試商店", 55),
        ("電子發票證明聯", 135),
        ("日期 2024/11/04", 290),
        ("御飯糰 1 x 35 35", 460),
        ("拿鐵 65", 560),
        ("總計 100", 850),
    ]:
        draw.text((70, y), text, font=font, fill="black")
    receipt = scan_image(np.asarray(image)[:, :, ::-1].copy(), mode="ocr")
    assert receipt is not None
    assert receipt.merchant_name == "合成測試商店"
    assert receipt.date == "2024-11-04"
    assert receipt.total == 100
    validate_payload(receipt)


@pytest.mark.skipif(not FONT.exists(), reason="Microsoft JhengHei font is unavailable")
@pytest.mark.parametrize("template_id", ["tw_post_stamp_purchase", "tw_no_uniform_invoice_receipt"])
def test_builtin_templates_with_real_ocr(template_id, validate_payload):
    if template_id == "tw_post_stamp_purchase":
        image = Image.new("RGB", (1800, 900), "#eff8ed")
        texts = [
            ("購買票品證明單", 600, 70),
            ("第 001234 號", 1370, 90),
            ("茲證明 合成買方", 100, 240),
            ("本日確經購買", 920, 240),
            ("郵票", 1430, 240),
            ("計新臺幣 壹萬元整", 100, 340),
            ("NT: 10,000.00", 170, 440),
            ("104.05.06-13", 1370, 410),
            ("證明郵局郵戳", 1380, 550),
        ]
    else:
        image = Image.new("RGB", (1800, 1500), "white")
        texts = [
            ("免用統一發票收據", 750, 35),
            ("統一編號 11223344", 1080, 150),
            ("合成買方 台照", 100, 200),
            ("109年10月15日", 950, 275),
            ("品 名", 140, 390),
            ("數量", 620, 390),
            ("單價", 820, 390),
            ("總價", 1040, 390),
            ("備註", 1450, 390),
            ("合成商品", 140, 520),
            ("2", 650, 520),
            ("290", 840, 520),
            ("580", 1050, 520),
            ("合成小店", 1420, 650),
            ("統一編號", 1420, 740),
            ("55667788", 1420, 830),
            ("合計 NT$ 580", 100, 1250),
        ]
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT), 42)
    for text, x, y in texts:
        draw.text((x, y), text, font=font, fill="black")
    receipt = scan_image(np.asarray(image)[:, :, ::-1].copy(), mode="ocr")
    assert receipt is not None and receipt.template_id == template_id
    if template_id == "tw_post_stamp_purchase":
        assert receipt.total == 10000 and receipt.date == "2015-05-06"
        assert receipt.items[0].name == "郵票"
    else:
        assert receipt.total == 580 and receipt.date == "2020-10-15"
        assert receipt.merchant_tax_id == "55667788"
        assert receipt.items[0].name == "合成商品"
    validate_payload(receipt)
