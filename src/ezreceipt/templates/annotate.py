"""Template authoring geometry and a thin Tk annotation window."""

import hashlib
import json
import re
import unicodedata
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from .._tk import enable_dpi_awareness
from ..extractors.ocr import OcrEngine, OcrLine, group_rows
from ..imaging import deskew, detect_document_quad
from ..normalize import parse_date, parse_money
from .matcher import has_label
from .model import Template

FIELD_CHOICES = {
    "date": ("日期", "date", ["日期", "中華民國"]),
    "time": ("時間", "time", ["時間"]),
    "merchant_name": ("店家名稱", "text", ["營業人名稱", "店名", "商號"]),
    "merchant_tax_id": ("統一編號", "tax_id", ["統一編號", "統編"]),
    "invoice_number": ("發票號碼", "text", ["發票號碼"]),
    "subtotal": ("小計", "money", ["小計"]),
    "tax": ("稅額", "money", ["稅額", "營業稅"]),
    "total": ("總金額", "money", ["總計", "合計", "總金額", "應付"]),
    "items": ("品項表格", "items", []),
    "custom": ("自訂欄位", "text", []),
}


@dataclass(frozen=True)
class Annotation:
    key: str
    label: str
    target: str
    type: str
    box: tuple[float, float, float, float]


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", unicodedata.normalize("NFKC", text).lower()).strip("_")
    return slug or "field_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def canvas_to_normalized(
    coords: tuple[float, float, float, float], size: tuple[int, int]
) -> tuple[float, float, float, float]:
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError("Canvas dimensions must be positive")
    x0, x1 = sorted((max(0.0, min(1.0, coords[0] / width)), max(0.0, min(1.0, coords[2] / width))))
    y0, y1 = sorted(
        (max(0.0, min(1.0, coords[1] / height)), max(0.0, min(1.0, coords[3] / height)))
    )
    if x0 == x1 or y0 == y1:
        raise ValueError("Box must have positive area")
    return x0, y0, x1, y1


def normalized_to_canvas(
    box: tuple[float, float, float, float], size: tuple[int, int]
) -> tuple[float, float, float, float]:
    return tuple(value * size[i % 2] for i, value in enumerate(box))


def remove_annotation(annotations: list[Annotation], index: int) -> list[Annotation]:
    if not 0 <= index < len(annotations):
        raise ValueError("No annotation selected")
    return annotations[:index] + annotations[index + 1 :]


def suggest_keywords(lines: list[OcrLine]) -> list[str]:
    return list(
        dict.fromkeys(
            line.text.strip()
            for line in sorted(lines, key=lambda entry: entry.box[1])
            if line.box[1] <= 0.2
            and 2 <= len(line.text.strip()) <= 40
            and (chars := [c for c in line.text if c.isalnum()])
            and sum(c.isdigit() for c in chars) <= len(chars) / 2
            and not re.search(r"\d{4,}", line.text)
            and not parse_date(line.text)
            and parse_money(line.text) is None
            and not re.search(
                r"(?:NT\s*[:$]|TWD|[$＄¥￥]|總計|合計|金額|應付)\s*[:：]?\s*\d"
                r"|\d[\d,.]*\s*[元圓]"
                r"|[零〇一二三四五六七八九十百千萬億壹貳參肆伍陸柒捌玖拾佰仟兩]+[元圓]"
                r"|\d+\.\d{2}(?!\d)",
                line.text,
                re.I,
            )
            and re.search(r"[A-Za-z\u3400-\u9fff]", line.text)
        )
    )[:3]


def build_template(
    template_id: str,
    name: str,
    annotations: list[Annotation],
    keywords: list[str],
    lines: Sequence[OcrLine] = (),
) -> Template:
    fields = []
    rows = group_rows(list(lines))
    for annotation in annotations:
        locators = [{"strategy": "region", "box": list(annotation.box)}]
        labels = FIELD_CHOICES.get(annotation.target, ("", "", []))[2]
        cy = (annotation.box[1] + annotation.box[3]) / 2
        for row in rows:
            if not min(line.box[1] for line in row) <= cy <= max(line.box[3] for line in row):
                continue
            found = next(
                (
                    (line, label)
                    for line in row
                    for label in labels
                    if has_label(line.text, [label])
                ),
                None,
            )
            if found:
                line, label = found
                direction = "right" if annotation.box[0] >= line.box[0] else "left"
                locators.append({"strategy": "anchor", "labels": [label], "direction": direction})
                break
        fields.append(
            {
                "key": annotation.key,
                "label": annotation.label,
                "target": annotation.target,
                "type": annotation.type,
                "locate": locators,
            }
        )
    return Template(
        {
            "format_version": 1,
            "id": template_id,
            "name": name,
            "description": "使用標註工具建立",
            "defaults": {"currency": "TWD"},
            "match": {
                "keywords_any": keywords,
                "min_any": 1 if keywords else 0,
                "reference_min_inliers": 25,
            },
            "reference_image": "reference.jpg",
            "notes": "",
            "fields": fields,
        }
    )


def save_template(template: Template, templates_dir: str | Path, image: np.ndarray) -> Path:
    directory = Path(templates_dir) / template.id
    if directory.exists():
        raise ValueError(f"Template directory already exists: {directory}")
    ok, encoded = cv2.imencode(".jpg", image)
    if not ok:
        raise OSError("Cannot encode reference image")
    directory.mkdir(parents=True)
    encoded.tofile(directory / "reference.jpg")
    path = directory / "template.json"
    path.write_text(
        json.dumps(template.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def read_image(path: str | Path) -> np.ndarray:
    data = np.fromfile(path, np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if image is None:
        raise ValueError(f"Cannot read image: {path}")
    return image


def capture_sample(camera: int) -> np.ndarray:
    import tkinter as tk

    from PIL import Image, ImageTk

    from ..camera import FrameSource
    from ..trigger import AutoTrigger

    try:
        enable_dpi_awareness()
        root = tk.Tk()
    except tk.TclError as exc:
        raise RuntimeError(f"Cannot start template window: {exc}") from exc
    root.title("自動擷取模板樣本")
    view, status = tk.Label(root), tk.Label(root, text="等待收據…")
    view.pack()
    status.pack()
    trigger, result = AutoTrigger(), []
    root.bind("<space>", lambda _: trigger.force_capture())
    root.bind("<Escape>", lambda _: root.destroy())
    with FrameSource(camera) as source:
        previous = None

        def tick() -> None:
            nonlocal previous
            sample = source.read()
            if sample and sample[1] != previous:
                frame, previous = sample
                update = trigger.update(frame, previous)
                status.configure(text=update.message)
                if update.captured is not None:
                    result.append(update.captured)
                    root.destroy()
                    return
                image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                image.thumbnail((960, 650))
                view.image = ImageTk.PhotoImage(image)
                view.configure(image=view.image)
            root.after(30, tick)

        tick()
        root.mainloop()
    if not result:
        raise ValueError("Sample capture cancelled")
    return result[0]


def run_annotator(
    image: np.ndarray,
    templates_dir: str | Path,
    template_id: str | None = None,
    name: str | None = None,
) -> Path | None:
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk

    from PIL import Image, ImageTk

    try:
        enable_dpi_awareness()
        root = tk.Tk()
    except tk.TclError as exc:
        raise RuntimeError(f"Cannot start template window: {exc}") from exc
    root.title("EzReceipt 模板標註")
    quad = detect_document_quad(image, min_area=0.2, brightness_gap=12)
    if quad is not None and messagebox.askyesno("影像校正", "使用校正後影像？", parent=root):
        image = deskew(image, quad)
    preview = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    preview.thumbnail((900, 700))
    size = preview.size
    canvas = tk.Canvas(root, width=size[0], height=size[1], cursor="crosshair")
    canvas.pack(side="left")
    photo = ImageTk.PhotoImage(preview)
    canvas.create_image(0, 0, image=photo, anchor="nw")
    panel = ttk.Frame(root, padding=12)
    panel.pack(side="right", fill="both")
    ttk.Label(panel, text="拖曳方框選取欄位；選取清單後按 Delete 刪除。").pack()
    listing = tk.Listbox(panel, width=32, exportselection=False)
    listing.pack(fill="both", expand=True)
    keyword_text = tk.StringVar()
    ttk.Label(panel, text="辨識關鍵字（以逗號分隔）").pack()
    ttk.Entry(panel, textvariable=keyword_text, width=35).pack(fill="x")
    status = tk.StringVar(value="載入 OCR 中，可先標註欄位…")
    ttk.Label(panel, textvariable=status, wraplength=280).pack(fill="x", pady=10)
    annotations, rectangles, lines, result = [], [], [], []
    engine, executor = OcrEngine(), ThreadPoolExecutor(max_workers=1)
    pending = [(executor.submit(engine.recognize, image), "sample")]
    start, draft = None, None

    def field_dialog(box: tuple) -> Annotation | None:
        dialog = tk.Toplevel(root)
        dialog.title("欄位設定")
        dialog.transient(root)
        target = tk.StringVar(value="total")
        options = list(FIELD_CHOICES)
        ttk.Label(dialog, text="欄位").grid(row=0, column=0, padx=10, pady=8)
        choice = ttk.Combobox(
            dialog,
            values=[f"{k} / {FIELD_CHOICES[k][0]}" for k in options],
            state="readonly",
            width=32,
        )
        choice.current(options.index("total"))
        choice.grid(row=0, column=1)
        label, key, kind = tk.StringVar(value="總金額"), tk.StringVar(), tk.StringVar(value="text")
        ttk.Label(dialog, text="標籤").grid(row=1, column=0)
        ttk.Entry(dialog, textvariable=label).grid(row=1, column=1)
        ttk.Label(dialog, text="自訂 key").grid(row=2, column=0)
        key_entry = ttk.Entry(dialog, textvariable=key)
        key_entry.grid(row=2, column=1)
        ttk.Label(dialog, text="自訂型別").grid(row=3, column=0)
        type_entry = ttk.Combobox(
            dialog,
            textvariable=kind,
            values=["text", "number", "money", "date", "time", "tax_id"],
            state="readonly",
        )
        type_entry.grid(row=3, column=1)
        answer = []

        def change(_: object = None) -> None:
            target.set(options[choice.current()])
            label.set(FIELD_CHOICES[target.get()][0])
            custom = target.get() == "custom"
            key_entry.configure(state="normal" if custom else "disabled")
            type_entry.configure(state="readonly" if custom else "disabled")

        def accept() -> None:
            t = target.get()
            k = (key.get().strip() or slugify(label.get())) if t == "custom" else t
            if not re.fullmatch(r"[a-z0-9_]+", k) or not label.get().strip():
                messagebox.showerror(
                    "欄位錯誤", "key 請使用 a-z、0-9、底線，標籤不可空白。", parent=dialog
                )
                return
            if any(a.key == k for a in annotations):
                messagebox.showerror("欄位重複", "此 key 已存在，請先刪除原方框。", parent=dialog)
                return
            answer.append(
                Annotation(
                    k,
                    label.get().strip(),
                    t,
                    kind.get() if t == "custom" else FIELD_CHOICES[t][1],
                    box,
                )
            )
            dialog.destroy()

        choice.bind("<<ComboboxSelected>>", change)
        change()
        ttk.Button(dialog, text="加入欄位", command=accept).grid(row=4, column=1, pady=10)
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        dialog.grab_set()
        root.wait_window(dialog)
        return answer[0] if answer else None

    def press(event: object) -> None:
        nonlocal start, draft
        start = (event.x, event.y)
        draft = canvas.create_rectangle(*start, *start, outline="#0066aa", width=2)

    def drag(event: object) -> None:
        if start is not None:
            canvas.coords(draft, *start, event.x, event.y)

    def release(event: object) -> None:
        nonlocal start, draft
        if start is None:
            return
        try:
            box = canvas_to_normalized((*start, event.x, event.y), size)
            annotation = field_dialog(box)
            if annotation:
                annotations.append(annotation)
                rectangles.append(draft)
                canvas.coords(draft, *normalized_to_canvas(box, size))
                listing.insert("end", f"{annotation.label} ({annotation.key})")
                x0, y0, x1, y1 = normalized_to_canvas(box, (image.shape[1], image.shape[0]))
                crop = image[
                    int(y0) : max(int(y0) + 1, int(y1)), int(x0) : max(int(x0) + 1, int(x1))
                ].copy()
                pending.append((executor.submit(engine.recognize, crop), annotation.label))
                status.set(f"辨識 {annotation.label} 中…")
                draft = None
        except ValueError as exc:
            status.set(str(exc))
        finally:
            if draft is not None:
                canvas.delete(draft)
            start, draft = None, None

    def delete(_: object = None) -> None:
        if listing.curselection():
            index = listing.curselection()[0]
            annotations[:] = remove_annotation(annotations, index)
            canvas.delete(rectangles.pop(index))
            listing.delete(index)

    def select(_: object = None) -> None:
        selected = listing.curselection()
        for i, rectangle in enumerate(rectangles):
            canvas.itemconfigure(
                rectangle,
                outline="#b34000" if selected and selected[0] == i else "#0066aa",
                width=3 if selected and selected[0] == i else 2,
            )

    def save() -> None:
        if not annotations:
            status.set("請先標註至少一個欄位。")
            return
        tid = template_id or simpledialog.askstring(
            "模板代碼",
            "id（a-z、0-9、底線）",
            initialvalue="receipt_" + uuid4().hex[:8],
            parent=root,
        )
        if not tid:
            return
        title = name or simpledialog.askstring(
            "模板名稱", "顯示名稱", initialvalue=tid, parent=root
        )
        if not title:
            return
        try:
            keywords = [s.strip() for s in re.split(r"[,，]", keyword_text.get()) if s.strip()]
            template = build_template(tid, title, annotations, keywords, lines)
            result.append(save_template(template, templates_dir, image))
            root.destroy()
        except (OSError, ValueError) as exc:
            messagebox.showerror("儲存失敗", str(exc), parent=root)

    def poll() -> None:
        for future, label in pending[:]:
            if not future.done():
                continue
            pending.remove((future, label))
            try:
                recognized = future.result()
                if label == "sample":
                    lines[:] = recognized
                    if not keyword_text.get():
                        keyword_text.set(", ".join(suggest_keywords(lines)))
                    status.set("拖曳選取欄位範圍。")
                else:
                    status.set(
                        f"{label}：{' '.join(line.text for line in recognized) or '未讀到文字'}"
                    )
            except Exception as exc:
                status.set(f"OCR 失敗：{exc}")
        root.after(80, poll)

    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", release)
    listing.bind("<<ListboxSelect>>", select)
    root.bind("<Delete>", delete)
    root.bind("<Escape>", lambda _: root.destroy())
    ttk.Button(panel, text="刪除選取欄位", command=delete).pack(fill="x")
    ttk.Button(panel, text="儲存模板", command=save).pack(fill="x", pady=6)
    root.after(80, poll)
    try:
        root.mainloop()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return result[0] if result else None


def new_template(
    *,
    image: str | None,
    camera: int | None,
    templates_dir: str | Path,
    template_id: str | None = None,
    name: str | None = None,
) -> Path | None:
    sample = read_image(image) if image is not None else capture_sample(camera or 0)
    return run_annotator(sample, templates_dir, template_id, name)
