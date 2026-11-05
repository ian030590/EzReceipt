# EzReceipt 接收 API 1.0

接收端自行提供 HTTP 或 HTTPS URL。EzReceipt 對該 URL 發出 POST，
body 為 UTF-8 JSON，一次一筆收據。接收端完成保存後，回覆任意 2xx 即表示成功。
資料辨識在本機完成；只有這個使用者指定的 webhook 會收到資料。

## 傳輸與重試

| HTTP 標頭 | 值與用途 |
| --- | --- |
| `Content-Type` | `application/json; charset=utf-8` |
| `User-Agent` | `EzReceipt/0.1.0`，版本隨套件更新 |
| `X-EzReceipt-Event` | `receipt.captured` |
| `Idempotency-Key` | 與 body 的 `receipt_id` 相同 |
| `Authorization` | 設有 token 時為 `Bearer <token>`，否則不送出 |

預設每次請求逾時 10 秒，首次失敗後最多重試 3 次，等待 1、2、4 秒。
連線錯誤、逾時、HTTP 429 及 5xx 會重試，其餘 4xx 不重試。
3xx 重新導向視為失敗，不跟隨，以免權杖或資料被轉送到其他位置。
重試使用相同的 body 與 `receipt_id`。最終失敗時 `WebhookSink.send()`
會拋出 `DeliveryError`；監看模式會記錄錯誤並繼續其他輸出。
沒有持久化的傳送佇列。需要備份可同時指定 `--jsonl`。

預設 URL 與權杖可由 `EZRECEIPT_WEBHOOK_URL`、`EZRECEIPT_WEBHOOK_TOKEN`
提供；命令列參數優先。接收端應自行驗證權杖並設定存取權限。

## Payload 欄位

所有下表欄位每次都會出現。未知的可空欄位為 JSON `null`，
清單沒有內容時為 `[]`，不以空字串表示未知數值。
金額四捨五入到小數兩位，整數值輸出為 JSON 整數；
數量及非金額自訂數值保留原有精度。

| 欄位 | 型別 | 意義與空值規則 |
| --- | --- | --- |
| `schema_version` | string | 固定 `"1.0"` |
| `event` | string | 固定 `"receipt.captured"` |
| `receipt_id` | string | 每次辨識事件的 UUID4，不為 null |
| `captured_at` | string | 含本機時區偏移的 ISO 8601 時間，不為 null |
| `source` | string | `tw_einvoice_qr` 或 `local_ocr` |
| `template_id` | string / null | 匹配或強制套用的模板代碼；未使用模板時為 null |
| `template_name` | string / null | 模板顯示名稱；未使用模板時為 null |
| `merchant_name` | string / null | 店名，常見簡體字轉為繁體；QR 不包含店名 |
| `merchant_tax_id` | string / null | 賣方八碼統編，保留開頭的 0；全 0 或未知為 null |
| `buyer_tax_id` | string / null | 買方八碼統編；全 0 或未知為 null |
| `invoice_number` | string / null | 兩個英文字母加八位數字，不含連字號；未知為 null |
| `date` | string / null | 收據日期，西元 `YYYY-MM-DD`；民國已轉換；未知為 null |
| `time` | string / null | 收據時間 `HH:MM:SS`；只有時分時補秒 00；未知為 null |
| `currency` | string | 三碼幣別，預設 `TWD`；一般 OCR 不推測外幣 |
| `items` | array | 品項物件清單；無法辨識時為 `[]` |
| `item_count` | integer | `items` 的實際長度，不是 QR 宣告數量 |
| `subtotal` | number / null | 未稅或小計金額；未知為 null |
| `tax` | number / null | 稅額；QR 為總額減未稅金額；未知為 null |
| `total` | number / null | 應付總額，排除現金、找零等付款欄位；未知為 null |
| `custom_fields` | array | 模板自訂欄位清單；未使用模板時為 `[]`，程式庫也可自行填入 |
| `confidence` | number | 0 到 1 的規則分數，並非經統計校準的正確率 |
| `warnings` | string[] | 警告代碼，沒有警告時為 `[]` |
| `image_file` | string / null | 啟用照片保存且成功時的本機檔案路徑，否則為 null |
| `pdf_file` | string / null | 啟用 `--save-pdf` 且成功時的本機 PDF 路徑，否則為 null |

每個品項都包含以下四個鍵：

| 品項欄位 | 型別 | 意義 |
| --- | --- | --- |
| `name` | string | 品名 |
| `quantity` | number / null | 數量，未知為 null |
| `unit_price` | number / null | 單價，未知為 null |
| `amount` | number / null | 品項金額，未知但數量與單價皆已知時以乘積計算 |

自訂欄位形狀為
`{"key": "serial_no", "label": "交易序號", "value": "A12345"}`。
`key` 是符合 `[a-z0-9_]+` 的機器識別碼，`label` 是顯示名稱，
`value` 是 string、number 或 null。三個鍵都必須存在。
模板已宣告但未讀到的自訂欄位仍會出現，`value` 為 null；
模板的 `check` 核對欄位不會輸出。`template_name` 與 `label` 可以調整，
接收端應使用 `template_id` 與 `key` 作為識別碼。
這些欄位的來源是 OCR 模板，`source` 仍為 `local_ocr`；QR 合併成功時為 `tw_einvoice_qr`。

內建模板的自訂欄位如下；選填但未讀到時仍為 null，且沒有缺欄警告。

| template_id | custom_fields 的 key、型別與意義 |
| --- | --- |
| `tw_post_stamp_purchase` | `serial_no` 證明單號碼、`buyer` 購買人、`post_office` 郵局（皆為文字） |
| `tw_no_uniform_invoice_receipt` | `buyer` 買受人（文字） |
| `tw_payment_receipt` | `payer` 付款單位（文字）；選填 `withholding_tax` 所得稅額、`nhi_premium` 補充健保費、`net_amount` 實領（整數 TWD） |
| `tw_cash_register_invoice` | `period` 期別（文字，例如 `115年01-02月`） |
| `tw_thsr_ticket_proof` | `from_station` 起程站、`to_station` 到達站、`ticket_no` 卡號／票號；選填 `control_no` 控管編號、`ticket_type` 票證類別（皆為文字，保留完整數字） |

領款模板的 merchant_name 為領款人、品項為用途，不擷取身分證字號、電話、地址或帳號。
高鐵模板的 date/time 只取乘車日期；列印日期與付款日期不作後備來源。

## 來源、優先順序與警告

`tw_einvoice_qr` 表示臺灣電子發票 QR 解碼成功。發票號碼、日期、統編、
小計、稅額、總額與完整 QR 品項優先採用 QR，OCR 可補店名、時間及缺少欄位。
QR 品項不完整時，若 OCR 有更多品項則採用 OCR 品項清單。
`local_ocr` 表示透過 RapidOCR 與模板或通用規則辨識。
沒有 QR 且沒有總額、品項或日期時，不產生事件；部分資料仍可產生事件。

| 警告代碼 | 意義 |
| --- | --- |
| `einvoice_items_incomplete` | 解出的 QR 品項少於宣告總數，可能只拍到其中一個 QR |
| `total_missing` | 找不到總額，`total` 為 null |
| `date_missing` | 找不到有效日期，`date` 為 null |
| `date_year_inferred` | 年月日格式的兩位民國年 YY 小於 60，補上遺失的百位 1 後落在當年民國年 ±1 年內；例如當年為 115 年時，15年6月30日輸出 2026-06-30 |
| `ocr_failed` | 自動模式的 OCR 執行失敗，但 QR 仍辨識成功 |
| `image_save_failed` | 使用者啟用影像保存，但編碼或寫入失敗 |
| `pdf_save_failed` | 使用者啟用 PDF 保存，但產生或寫入失敗 |
| `total_from_items` | 原總額缺少，已將可讀的品項金額加總作為總額 |
| `total_from_currency_mark` | 沒有標示總額；所有 NT$／NT／新台幣／新臺幣金額只有一種不同數值，採用該值 |
| `total_from_payment` | 收銀機模板總計、項數列、應收金額與銷售總額皆不可讀，改以現金列金額作為總額 |
| `items_total_mismatch` | 可讀品項金額合計與總額相差超過 0.5 元；保留原總額 |
| `cross_check_mismatch:<key>` | 模板欄位與核對欄位不同；保留主欄位 |
| `template_field_missing:<key>` | 該模板非 optional 欄位未讀到；啟用通用補值的模板可能之後補足 |
| `template_default_used:<key>` | 已匹配模板，欄位未讀到而採用欄位的 default |

QR 資料完整時分數為 0.98，品項不完整為 0.8。
通用 OCR 分數為 `0.3 + 0.4 × 平均辨識分數`，日期或總額每缺一項減 0.1，
最後限制在 0.05 到 0.9。請依用途人工覆核低分或有警告的收據。
模板分數為 0.75，日期、總額各缺少時減 0.1（品項補算總額前評分）。
品項補算與金額核對在 pipeline 合併後統一執行；不會改寫已讀到的總額。
品項合計等於 subtotal，且 subtotal + tax 等於 total 時，視為未稅品項與含稅總額相符，
不產生 `items_total_mismatch`。
接收端應保留未知警告代碼，以便未來版本增加警告。

## 完整範例

以下資料全部為合成範例，非真實交易：

```json
{
  "schema_version": "1.0",
  "event": "receipt.captured",
  "receipt_id": "0b6c0000-0000-4000-8000-000000000001",
  "captured_at": "2026-11-04T23:10:05+08:00",
  "source": "tw_einvoice_qr",
  "template_id": null,
  "template_name": null,
  "merchant_name": "合成測試商店",
  "merchant_tax_id": "01234567",
  "buyer_tax_id": null,
  "invoice_number": "AB11223344",
  "date": "2024-11-04",
  "time": "12:30:00",
  "currency": "TWD",
  "items": [
    {"name": "合成商品甲", "quantity": 1, "unit_price": 100, "amount": 100},
    {"name": "合成商品乙", "quantity": 2, "unit_price": 120, "amount": 240}
  ],
  "item_count": 2,
  "subtotal": 324,
  "tax": 16,
  "total": 340,
  "custom_fields": [{"key": "serial_no", "label": "交易序號", "value": "A12345"}],
  "confidence": 0.98,
  "warnings": [],
  "image_file": null,
  "pdf_file": null
}
```

機器可讀的 JSON Schema 使用 draft 2020-12，隨套件附於
[`receipt-1.0.schema.json`](../src/ezreceipt/schema/receipt-1.0.schema.json)。
Python 可用 `importlib.resources.files("ezreceipt").joinpath("schema/receipt-1.0.schema.json")`
讀取。不應依賴 JSON 鍵的排列順序。

## 冪等與版本

接收端應以 `receipt_id` 做唯一鍵，在同一筆資料重送時回覆成功，避免重複寫入。
請在同一資料庫交易內檢查唯一鍵並寫入資料。
不同辨識事件可能對應同一張實體收據、具有不同 UUID；需要跨次掃描去重時，
可另外使用發票號碼或 `(date, total, merchant_name, template_id)`。
EzReceipt 的記憶體去重預設只保留 600 秒，重啟程式後不保留。

新增可選欄位、來源或警告等相容擴充留在 1.x；變更既有欄位語意、型別、
刪除欄位等破壞相容的變更升為 2.0。接收端應忽略未知的擴充欄位，
對未知主要版本明確拒絕或另行處理。目前輸出的版本固定為 1.0。
接收端必須忽略頂層、品項及自訂欄位物件中的未知欄位。
JSON Schema 在這三層皆允許額外屬性，既有 `required` 欄位仍須存在；
`Receipt.from_payload()` 同樣忽略未知屬性。
`pdf_file` 是後來加入的相容擴充：目前版本每次都會輸出，
但 JSON Schema 不將它列為必填，舊版產生的 payload 仍可通過驗證。

## Excel 對應建議

| 工作表 | 一列代表 | 建議欄位 |
| --- | --- | --- |
| `Receipts` | 一筆收據 | receipt_id、captured_at、date、time、merchant_name、merchant_tax_id、buyer_tax_id、invoice_number、currency、subtotal、tax、total、source、confidence、warnings |
| `Items` | 一個品項 | receipt_id、品項序號、name、quantity、unit_price、amount |

以 `receipt_id` 關聯兩張表。統編與發票號碼使用文字格式，保留開頭的 0。
JSON null 建議對應空白儲存格，金額用數值儲存；不要把未知金額寫成 0。
`custom_fields` 可展開成額外欄，欄名使用 `label`；若標籤重複，附加 `key` 區別。
警告清單可用分號串接，同時保留原始 JSON 以利追查。
`image_file`、`pdf_file` 是發送機的路徑，不代表接收機能讀取該檔案，
Webhook 不上傳影像或 PDF 本體。

## 本機測試

```powershell
python examples/webhook_receiver.py output/received.jsonl --port 8000
```

另一個終端機：

```powershell
python examples/make_sample_einvoice.py output/sample.png
ezreceipt scan output/sample.png --extractor einvoice --webhook http://127.0.0.1:8000/receipts
```

範例接收器僅使用 Python 標準函式庫，綁定 loopback，在 stdout 顯示並附加到指定 JSONL。
它在目前執行期間依 receipt_id 去重，但不驗證 bearer token，也不提供持久化唯一鍵；
正式接收端需自行實作這兩項。

