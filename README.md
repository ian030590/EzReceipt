# EzReceipt

Free, offline receipt recognition with automatic camera capture and webhook delivery.
Supports Taiwan e-invoice QR codes and local Traditional Chinese/English OCR.
Python 3.10+; Windows 11 is the primary target. MIT licensed.

EzReceipt 會監看攝影機。把收據放到鏡頭前、保持穩定後，程式會自動擷取，
辨識店名、日期、品項與金額，再以 HTTP POST 傳送結構化 JSON。
不需要按快門，也不需要雲端帳號、AI 視覺 API 或 LLM。
辨識完全離線且免費，RapidOCR 的 ONNX 模型在本機 CPU 執行。

## 安裝

需要 Python 3.10 以上，建議使用虛擬環境：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install git+https://github.com/ian030590/EzReceipt
```

開發環境安裝與檢查：

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
```

安裝套件需要網路；安裝後的辨識不需要網路。請使用包含 PP-OCRv6 模型的
RapidOCR 3.10 以上、4.0 以下版本。EzReceipt 只載入套件內現有模型，不會下載模型；
模型缺少時會記錄錯誤，QR 辨識仍可使用。

QR 優先使用 `zxing-cpp>=2.2,<4`（Apache-2.0，Win/Linux/macOS wheels，離線解碼）；
未安裝或未找到電子發票標頭時改用 OpenCV。支援 Big5、UTF-8 與 Base64 品名。
OCR 輸入先取 B/G/R 最小通道並以 sigma 1.0 輕微模糊，以改善彩色點陣列印；
文件校正、對齊與 QR 輸入不套用這個 OCR 轉換。
收據必須正向放置。文件校正可處理小幅傾斜；不支援上下顛倒的收據。
OCR 預設停用逐行 180° 方向分類（`use_cls=False`），避免手寫金額被翻轉後誤讀。

OpenCV 必須使用 `opencv-python-headless`，不要另外安裝 `opencv-python`，
兩者共用 `cv2`，同時安裝會衝突。預覽使用 Tkinter 與 Pillow，
不使用 OpenCV 視窗。部分 Linux 發行版需另外安裝系統的 Tk 套件；
沒有顯示環境時可使用 `--no-preview`，Tk 啟動失敗也會自動改為無預覽模式。

## 快速開始

開啟本機測試接收器，再於另一個終端機啟動監看：

```powershell
python examples/webhook_receiver.py output/receipts.jsonl
ezreceipt watch --webhook http://localhost:8000/receipts
```

收據進入畫面後，程式會偵測明亮紙張的矩形邊界或 QR Code，
等畫面連續穩定 8 格，再選擇最清晰的一格辨識。擷取後會等待移開收據，
或換入明顯不同的收據，避免同一張重複擷取。
建議使用 1080p 攝影機、均勻照明，攤平收據、避免反光，並至少穩住約 0.5 秒；
實際等待時間取決於攝影機與分析速度。Space 可手動擷取，Q 或 Esc 可離開。

```powershell
ezreceipt cameras
ezreceipt watch --camera 0 --width 1920 --height 1080 --backend auto --stdout
ezreceipt watch --source demo.mp4 --no-preview --jsonl output/receipts.jsonl
ezreceipt watch --save-images captures --dedupe-window 600
ezreceipt scan sample.png --extractor einvoice
ezreceipt scan first.jpg second.jpg --extractor ocr --jsonl output/receipts.jsonl
```

`watch` 未指定輸出時使用 stdout，每筆一行 JSON。
`scan` 一律輸出 JSON 到 stdout，也可同時指定 webhook 或 JSONL。
完全沒有辨識到資料時，`scan` 結束碼為 1；讀檔或傳送失敗也回傳 1。
`watch --dedupe-window 0` 可停用同一工作階段的重複辨識過濾。
Windows 的 `--backend` 可選 `auto`、`msmf` 或 `dshow`；`auto` 先嘗試 MSMF 再嘗試 DSHOW。
Ctrl+C 會停止擷取，並等待已排入佇列的辨識與傳送完成。

可用環境變數設定 webhook：

```powershell
$env:EZRECEIPT_WEBHOOK_URL = "http://localhost:8000/receipts"
$env:EZRECEIPT_WEBHOOK_TOKEN = "your-own-token"
ezreceipt watch
```

命令列的 `--webhook`、`--webhook-token` 優先於環境變數。不要把真實權杖寫入版本庫。

## 辨識結果與限制

- `auto`：先讀臺灣電子發票 QR，再以 OCR 補上店名、時間與缺少欄位。
- `einvoice`：只讀 QR，速度較快且不載入 OCR。QR 沒有店名，因此店名通常為 null。
- `ocr`：只做本機 OCR，先匹配模板，沒有匹配時使用通用規則。

電子發票 QR 的日期、統編及金額是直接解碼的資料；完整的 QR 品項優先於 OCR。
OCR 及規則推測可能讀錯、漏讀，請檢查 `confidence`、`warnings` 與原收據。
僅有部分資料時仍會輸出；若連日期、總額或品項都沒有，則不輸出。
手寫字、皺摺、反光、特殊排版、多行品項名稱與非常小的字可能降低準確度。
品項金額存在但總額缺少時，會加總品項並標示 `total_from_items`；
品項加總與總額相差超過 0.5 元時，會標示 `items_total_mismatch`。
若品項合計等於未稅銷售額，且加上營業稅後等於總額，則視為相符。

產生不含真實資料的測試電子發票：

```powershell
python -m pip install segno
python examples/make_sample_einvoice.py output/sample.png
ezreceipt scan output/sample.png --extractor einvoice
```

## 收據模板

預設啟用五個內建模板：

- `tw_post_stamp_purchase`：中華郵政購買票品證明單。
- `tw_no_uniform_invoice_receipt`：免用統一發票收據。
- `tw_payment_receipt`：領據／領款收據，含付款單位、用途及選填扣款金額。
- `tw_cash_register_invoice`：二聯式／三聯式收銀機統一發票，含期別及列印金額。
- `tw_thsr_ticket_proof`：台灣高鐵電子車票／購票證明，以乘車日期為準。

支援民國日期、中文金額格、買賣方統編區分與自訂欄位；手寫數字仍可能誤讀。
可複製內建模板調整，也可用 Tk 標註圖片建立自己的格式。

```powershell
ezreceipt template list
ezreceipt template test tw_post_stamp_purchase sample.jpg
ezreceipt template copy-builtin tw_no_uniform_invoice_receipt --templates-dir my-templates
ezreceipt template new --image sample.jpg --templates-dir my-templates --id my_shop
ezreceipt scan sample.jpg --templates-dir my-templates
ezreceipt watch --template tw_post_stamp_purchase
ezreceipt scan sample.jpg --no-templates
```

`--templates-dir` 優先於 `EZRECEIPT_TEMPLATES_DIR`，未指定時只載入內建模板，
不會建立預設儲存目錄。同 id 的使用者模板覆寫內建模板。
`--template ID` 強制套用，`--no-templates` 停用模板；兩者不可同時指定。
模板格式、定位策略、標註操作與擴充方法見 [模板文件](docs/TEMPLATES.md)。

## Python 使用方式

```python
from ezreceipt import JsonlSink, Pipeline, ReceiptScanner, WebhookSink, scan_image

receipt = scan_image("sample.png", mode="auto")
if receipt is not None:
    WebhookSink("http://localhost:8000/receipts").send(receipt)

scanner = ReceiptScanner(
    pipeline=Pipeline(mode="auto"),
    sinks=[JsonlSink("output/receipts.jsonl")],
    on_receipt=lambda receipt: None,  # 在此處理已辨識的收據
)
scanner.run(preview=True)
```

程式庫同樣接受 `templates_dir="my-templates"`、`template="my_shop"`、
`use_templates=False`。`Pipeline.last_strategy_report` 可查看每個模板欄位命中的策略。

`scan_image` 也接受 BGR 的 NumPy 陣列，圖片路徑支援中文。
程式庫可用 `scanner.start()`、`scanner.stop()` 管理背景執行緒。
回呼會從背景執行緒呼叫；更新自己的 GUI 時應交由主執行緒處理。
`Receipt.to_payload()` 與 `Receipt.from_payload()` 可轉換資料。

完整的 1.0 payload、HTTP 標頭、重試規則與 Excel 欄位對應請見 [API 文件](docs/API.md)。

## 隱私與儲存

除了使用者設定的 webhook，辨識過程不會對外連線。
程式不會預設儲存照片或辨識紀錄；指定 `--save-images` 或 `--jsonl`
才會寫入對應目錄。建立模板時會將參考照片與標註存入指定的 `--templates-dir`。
範例接收器也只寫入 argv 指定的 JSONL 路徑。
Webhook 重試與去重只保存在記憶體，沒有持久化待送佇列；傳送失敗會寫入日誌。
需要保留可重送的紀錄時，請同時使用 `--jsonl`。

採用 [MIT 授權](LICENSE)。
