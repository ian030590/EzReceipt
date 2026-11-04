"""Normalize receipt dates, money and identifiers without changing display text."""

import re
import unicodedata
from datetime import date

_TRADITIONAL_PAIRS = """
说說 统統 编編 负負 责責 计計 数數 银銀 国國 税稅 学學 发發 单單 价價 总總 额額 号號 买買
卖賣 证證 邮郵 币幣 处處 门門 业業 营營 电電 话話 务務 费費 项項 码碼 圆圓 实實 应應 据據
专專 区區 万萬 亿億 两兩 参參 陆陸 贰貳 叁參 时時 间間 年年 开開 关關 网網 页頁 账帳
帐帳 购購 货貨 贸貿 易易 资資 产產 财財 经經 济濟 结結 给給 纸紙 张張 条條 细細 类類
种種 标標 识識 别別 备備 注註 录錄 传傳 递遞 送送 收收 讫訖 请請 谢謝 对對 款款 汇匯
兑兌 换換 现現 寄寄 补補 办辦 盖蓋 章章 车車 运運 输輸 机機 场場 站站
馆館 餐餐 饮飲 饭飯 饼餅 面面 粉粉 汤湯 盐鹽 酱醬 茶茶 糖糖 鸡雞 鸭鴨 鹅鵝 猪豬
鱼魚 虾蝦 贝貝 鲜鮮 冻凍 肉肉 奶奶 蛋蛋 蔬蔬 菜菜 果果 苹蘋 龙龍 莓莓 桃桃 柠檸
橘橘 莲蓮 华華 东東 阳陽 云雲 乡鄉 镇鎮 县縣 岛島 湾灣 广廣 庆慶 宁寧 义義 丰豐
荣榮 兴興 乐樂 长長 远遠 达達 顺順 安安 宝寶 寿壽 纪紀 绍紹 绪緒 维維 练練 络絡
线線 终終 红紅 绿綠 蓝藍 黄黃 黑黑 灯燈 号號 点點 头頭 齐齊 进進 选選 连連 过過
这這 边邊 还還 迁遷 适適 递遞 迟遲 违違 遗遺 邮郵 邻鄰 阳陽 闭閉 问問 闻聞 阅閱
队隊 陈陳 郑鄭 刘劉 杨楊 赵趙 吴吳 苏蘇 马馬 冯馮 许許 孙孫 叶葉 罗羅 钢鋼
铁鐵 铜銅 铝鋁 锡錫 销銷 错錯 键鍵 银銀 锅鍋 锁鎖 镜鏡 针針 鞋鞋 织織 纺紡 绵綿
绸綢 绳繩 组組 级級 约約 纠糾 纷紛 纽紐 纳納 纵縱 纹紋 纯純 纱紗 纲綱 纷紛
见見 规規 视視 觉覺 览覽 观觀 触觸 订訂 认認 让讓 议議 记記 讲講 设設 访訪 诀訣
评評 试試 诚誠 详詳 询詢 该該 语語 误誤 诺諾 读讀 课課 调調 谈談 谱譜 谅諒 谨謹
车車 轮輪 轻輕 辆輛 辅輔 辉輝 辑輯 转轉 轴軸 轨軌 较較 载載 轿轎 厂廠 厅廳 历歷
压壓 厕廁 厦廈 厨廚 园園 围圍 图圖 团團 圣聖 块塊 坚堅 坝壩 报報 执執 扩擴 担擔
拣揀 拥擁 挂掛 损損 换換 摊攤 摄攝 摆擺 携攜 扫掃 护護 拟擬 拨撥 择擇 摘摘
"""
_TRADITIONAL = str.maketrans(
    {p[0]: p[1] for p in _TRADITIONAL_PAIRS.split() if len(p) == 2 and p[0] != p[1]}
)


def to_traditional(text: str) -> str:
    """Convert common receipt characters without context-dependent conversions."""
    return text.translate(_TRADITIONAL)


def normalize_text(text: str) -> str:
    return to_traditional(unicodedata.normalize("NFKC", text)).strip().replace("台", "臺")


def _numeric_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(
        r"[0-9OoIl]+",
        lambda m: (
            m[0].translate(str.maketrans("OoIl", "0011"))
            if any(c.isdigit() for c in m[0])
            else m[0]
        ),
        text,
    )


def parse_date(text: str) -> str | None:
    text = re.sub(r"\s+(?=\d{1,2}\s*:)", "T", _numeric_text(text))
    text = re.sub(r"(?<=[年月])\s*[(（|lI!\]】](?=\d)", "1", text)
    text = re.sub(r"\s+", "", text)
    pattern = r"(?<!\d)(\d{2,4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?(?!\d)"
    candidates = [m.groups() for m in re.finditer(pattern, text)]
    candidates.extend(
        m.groups() for m in re.finditer(r"(?<!\d)(\d{2,3})\.(\d{1,2})\.(\d{2})\d+", text)
    )
    for match in re.finditer(r"(?<!\d)(\d{7,8})(?!\d)", text):
        digits = match[0]
        candidates.append((digits[:-4], digits[-4:-2], digits[-2:]))
    for year_text, month, day in candidates:
        year = int(year_text)
        if len(year_text) <= 3:
            if not 60 <= year <= date.today().year - 1911 + 1:
                continue
            year += 1911
        elif not 1990 <= year <= date.today().year + 1:
            continue
        try:
            return date(year, int(month), int(day)).isoformat()
        except ValueError:
            continue
    return None


def parse_time(text: str) -> str | None:
    match = re.search(
        r"(?<!\d)(\d{1,2})\s*:\s*(\d{2})(?:\s*:\s*(\d{2}))?(?![\d:])",
        _numeric_text(text),
    )
    if match:
        hour, minute, second = (int(value or 0) for value in match.groups())
        if hour < 24 and minute < 60 and second < 60:
            return f"{hour:02}:{minute:02}:{second:02}"
    return None


_DIGITS = dict(zip("零〇一二兩三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
_DIGITS.update(dict(zip("壹貳參叁肆伍陸柒捌玖", (1, 2, 3, 3, 4, 5, 6, 7, 8, 9))))
_UNITS = {
    "十": 10,
    "拾": 10,
    "百": 100,
    "佰": 100,
    "千": 1000,
    "仟": 1000,
    "萬": 10000,
    "億": 100000000,
}


def parse_money(text: str) -> float | None:
    text = _numeric_text(text).strip()
    text = re.split(r"合[計计]|新[臺台][幣帶带币]|NT\$", text, flags=re.I)[-1]
    text = re.sub(r"(?i)TWD|[$圓整,\s]", "", text).strip(":：")
    units = [_UNITS[c] for c in text if c in "萬仟千佰百拾十"]
    grid = len(units) >= 3 and all(a > b for a, b in zip(units, units[1:]))
    if grid:
        total = 0
        numeral_seen = False
        coefficient = ""
        for char in text:
            if char in "萬仟千佰百拾十元":
                if len(coefficient) > 1:
                    return None
                digit = coefficient
                if digit in _DIGITS:
                    value = _DIGITS[digit]
                    numeral_seen = True
                elif digit.isascii() and digit.isdigit():
                    value = int(digit)
                    numeral_seen = True
                elif digit in ("", "—", "－", "-", "─", "ー", "✗", "✘", "×", "x", "X"):
                    value = 0
                else:
                    return None
                total += value * (1 if char == "元" else _UNITS[char])
                coefficient = ""
            else:
                coefficient += char
        if coefficient:
            if len(coefficient) != 1:
                return None
            if coefficient in _DIGITS:
                total += _DIGITS[coefficient]
                numeral_seen = True
            elif coefficient.isascii() and coefficient.isdigit():
                total += int(coefficient)
                numeral_seen = True
            elif coefficient not in "—－-─ー✗✘×xX":
                return None
        return float(total) if numeral_seen else None
    text = text.replace("元", "")
    if re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)", text):
        value = float(text)
        return value if abs(value) < float("inf") else None
    negative = text.startswith("-")
    text = text.lstrip("+-")
    if (
        not text
        or any(c not in _DIGITS and c not in _UNITS and not c.isdigit() for c in text)
        or not any(c in _DIGITS or c.isdigit() for c in text)
    ):
        return None
    total = section = 0
    number: int | None = None
    for char in text:
        if char.isdigit() or char in _DIGITS:
            digit = int(char) if char.isdigit() else _DIGITS[char]
            number = (number or 0) * 10 + digit
        else:
            unit = _UNITS[char]
            if unit < 10000:
                section += (1 if number is None else number) * unit
            elif unit == 10000:
                section = (section + (number or 0)) * unit
            else:
                total = (total + section + (number or 0)) * unit
                section = 0
            number = None
    value = float(total + section + (number or 0))
    return -value if negative else value


def parse_tax_id(text: str) -> str | None:
    match = re.search(r"(?<![A-Za-z0-9])(\d{8})(?![A-Za-z0-9])", _numeric_text(text))
    if match and match[1] != "00000000":
        return match[1]
    return None
