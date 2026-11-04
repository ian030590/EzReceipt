# 收據模板

模板描述一種收據格式的辨識條件與欄位位置。辨識完全在本機執行，
使用 OpenCV ORB、RapidOCR 與規則，不呼叫雲端服務。

## 直接使用

內建五種模板，`scan`、`watch` 預設啟用：

| id | 格式與欄位 |
| --- | --- |
| `tw_post_stamp_purchase` | 中華郵政購買票品證明單：日期、總額、買方統編、證明單號碼、購買人、郵局 |
| `tw_no_uniform_invoice_receipt` | 免用統一發票收據：日期、買賣方統編、店名、表格品項、總額、買受人 |
| `tw_payment_receipt` | 領據／領款收據：前 35% 的年月日、阿拉伯／中文總額、領款人、用途、付款單位及選填所得稅額／健保費／實領 |
| `tw_cash_register_invoice` | 二聯式／三聯式收銀機發票：前 20% 的發票號碼、期別、日期時間、買賣方統編、店名、銷售額／稅／總額、品項 |
| `tw_thsr_ticket_proof` | 高鐵電子車票／購票證明：乘車日期時間、票價、銷售額、稅、買方統編、車站、票號及選填控管編號／票證類別 |

郵政模板視票品為免稅、店家預設為中華郵政，沒有表格時以品名與總額建立單一品項。
免用發票模板將頂部統編視為買方、印章統編視為賣方，備註欄內的印章文字不列為品項。

領款模板以領款人為 merchant_name，用途為單一品項（缺少用途時為「領款」），金額等於總額。
不擷取身分證字號、電話、地址或帳號。此模板停用通用補值，避免個資欄混入輸出。
收銀機模板只採用各金額標籤的第一個列印列，空白手填欄不覆蓋前面的值。
總計不可讀時依序使用項數列金額、應收金額或銷售總額、現金金額；後者標示 `total_from_payment`。
日期欄沒有有效日期時，使用頂部 45% 內第一個有效日期列及其時間。
高鐵模板店名預設「台灣高速鐵路股份有限公司」、品項預設「高鐵車票」，
只讀乘車日期，不用列印日期或付款日期補值；單程紙票未命中時仍走通用解析。
這三個模板的金額使用整數新臺幣。郵政模板排除含「高鐵」或「車票」的文件。

```powershell
ezreceipt template list
ezreceipt template show tw_post_stamp_purchase
ezreceipt template test tw_post_stamp_purchase sample.jpg
ezreceipt scan sample.jpg --template tw_post_stamp_purchase
ezreceipt watch --no-templates
```

`template test` 輸出一個 JSON 物件，`receipt` 為完整 API payload 或 null，
`strategies` 為每個欄位成功使用的策略，無值時為 null、採用欄位預設時為 `default`。
`--template ID` 強制套用指定模板，即使關鍵字未匹配；無效代碼會報錯。
`--no-templates` 停用模板，不能與 `--template` 同時使用；QR-only 模式不執行模板。

## 檔案位置與覆寫

內建模板由 `src/ezreceipt/templates/builtin/<id>/template.json` 自動探索。
使用者模板位於明確指定的 `DIR/<id>/template.json`，可附 `reference.jpg`。
`--templates-dir DIR` 優先於環境變數 `EZRECEIPT_TEMPLATES_DIR`；沒有預設的使用者目錄。
同 id 的使用者模板覆寫內建模板。無效模板會顯示檔案路徑與驗證錯誤。

```powershell
ezreceipt template copy-builtin tw_no_uniform_invoice_receipt --templates-dir my-templates
ezreceipt template validate my-templates/tw_no_uniform_invoice_receipt/template.json
$env:EZRECEIPT_TEMPLATES_DIR = "my-templates"
ezreceipt scan sample.jpg
```

複製與標註儲存不覆寫既有目錄。修改已存在的 JSON 後可以再次 validate。
模板參考影像只保存在指定的資料夾，webhook 不會傳送影像。

## 用圖片或攝影機標註

```powershell
ezreceipt template new --image sample.jpg --templates-dir my-templates --id my_shop --name "合成商店收據"
ezreceipt template new --camera 0 --templates-dir my-templates
```

1. 攝影機模式先自動擷取；Space 可手動擷取、Esc 取消。圖片模式直接讀取樣本。
2. 找到可靠紙張邊界時會詢問「使用校正後影像？」；選擇後，以該影像作為參考座標。
3. 在畫布拖曳方框，選擇日期、時間、店名、統編、發票號碼、小計、稅額、總額、品項或自訂欄位。
4. 自訂欄位可輸入標籤、`key` 與型別。未填 key 時自動產生 slug；全中文標籤產生穩定的雜湊代碼。
5. 每次新增方框後，背景執行緒辨識區域，側欄顯示文字、空值或錯誤，模型暖機不阻塞 Tk。
6. 清單選取方框可反白，按 Delete 或「刪除選取欄位」移除。關鍵字預填頂部 OCR 文字，可編輯。
7. 儲存後寫入 `template.json` 與 `reference.jpg`，再以 `template test` 檢查。

標註工具主要產生 region 策略；同一行有已知欄位標籤時加上 anchor 後備策略。
可手動編輯 JSON 加入更可靠的 anchor、regex 或 table。
標註工具需要桌面 Tk 環境；幾何、刪除、建構、儲存與讀回邏輯皆可無顯示測試。

## JSON 格式（format_version 1）

```json
{
  "format_version": 1,
  "id": "my_shop",
  "name": "合成商店收據",
  "description": "示範格式，非真實交易",
  "defaults": {"currency": "TWD"},
  "match": {"keywords_all": ["收據"], "keywords_any": ["合成商店"], "min_any": 1,
            "reference_min_inliers": 25},
  "reference_image": "reference.jpg",
  "notes": "請確認手寫數字",
  "fields": [
    {"key": "total", "label": "總金額", "type": "money", "target": "total",
     "locate": [{"strategy": "anchor", "labels": ["合計"], "direction": "right"},
                {"strategy": "region", "box": [0.5, 0.7, 0.95, 0.9]}]},
    {"key": "serial_no", "label": "序號", "type": "text", "target": "custom",
     "locate": [{"strategy": "regex", "pattern": "序號\\s*([A-Z0-9]+)", "group": 1}]}
  ]
}
```

`id` 與欄位 `key` 使用 `[a-z0-9_]+`；同一模板不能有重複 key。
未知鍵、錯誤 target/type、無效 regex/group、越界或倒置方框皆拒絕載入。
`reference_image` 只能是模板目錄內的相對路徑；省略或 null 時也會尋找 `reference.jpg`。

`type`：`text | number | money | date | time | tax_id | items | invoice_number | period`。
日期輸出西元 ISO；民國年須為 2–3 位且介於 60 與當年民國年加一，
西元年介於 1990 與當年加一。`YY-MM-DD`／`YY/MM/DD` 僅在 YY 不大於今年後兩位加一、
且後接時間或前有「日期」／date 標籤時視為 20YY。
年月日格式的兩位民國年 YY 小於 60，且 YY+100 落在當年民國年 ±1 年時，
補上遺失的百位 1 並標示 `date_year_inferred`；其他格式不套用此推測。
日期分隔須一致，發票的「115年01-02月」是期別，不是日期。
`invoice_number` 合併空白／連字號並轉大寫；`period` 將「115年1-2月」標準化為「115年01-02月」。
金額支援大寫中文、折扣、位置金額格與常見數字 OCR 誤字。
空白、橫線、X 在萬仟佰拾元格內表示零；完全沒有數字或含未知字則為 null。
常見簡體字在標籤比對與文字輸出轉繁體，`台` 僅於匹配時視同 `臺`。

`target`：`date time merchant_name merchant_tax_id buyer_tax_id invoice_number subtotal tax total
items item_name currency custom check`，省略時為 `custom`。
`items` 型別與 target 必須成對。`custom` 輸出為 `{key, label, value}`，未知 value 為 null。
`item_name` 在沒有模板表格列時產生數量 1、金額為 total 的品項。
`check` 僅供核對，不輸出。標準純量欄位可由頂層 `defaults` 設定。

欄位可設定 `default`，僅在模板匹配／強制套用且讀取失敗時使用，
產生 `template_default_used:<key>`。`cross_check` 指向同模板另一個欄位 key；
兩者皆有值且不同時保留主欄位，產生 `cross_check_mismatch:<key>`。
欄位的 `reject_pattern` 為正規表示式；正規化後的候選值若匹配即捨棄，
繼續嘗試後續候選值、locator 或 default。無效正規表示式會拒絕載入。
`optional: true` 表示未讀到時不產生 `template_field_missing:<key>`；預設為 false。
選填自訂欄位仍輸出 null，不會憑空補成零。
頂層 `defaults` 也可指定 `item_name`，產生金額為 total、數量 1 的預設品項。
`generic_fallback: false` 僅使用模板明確宣告的欄位與 defaults；省略時維持通用補值。
內建領款、收銀機、高鐵模板使用 false，讓欄位來源與日期限制保持明確。

## 定位策略

`locate` 依序嘗試，第一個成功正規化的值勝出。標籤忽略空白、全半形及台／臺；
三字以上標籤容許一個字替換，兩字標籤精確匹配。anchor 優先採用精確標籤，避免短標籤誤中標題。

| strategy | 參數與行為 |
| --- | --- |
| `region` | `box: [x0,y0,x1,y1]`，0..1 座標，取中心在框內的 OCR 行；items 使用通用品項列解析 |
| `anchor` | `labels` 標籤清單；先取同框標籤後綴，再找指定方向最近的框；`max_gap` 預設 0.3 |
| `row` | `contains_all` 全部標籤、`contains_any` 至少一個；可用 `within` 限定區域；依左至右合併整列 |
| `regex` | `pattern` 與 `group`（預設 1，可設 0）；以「同列框以空格連接、列以換行連接」的文字匹配 |
| `table` | `columns`、`stop_labels`、`ignore_columns`，依表頭 x 座標將列分欄 |

regex 另可用 `within: [x0,y0,x1,y1]` 限定來源區域。
row 的 `after`／`before` 標籤限定起始列之後、結束列之前；`pattern`／`exclude_pattern`
以正規化後的整列文字篩選。`first: true` 僅嘗試第一個符合列，即使它是空白欄。
`last_money: true` 排除項數（項／件／個／pcs）與百分比，取整列最後一個金額。
`date_required: true` 只接受可解析為有效日期的列；與 `first: true` 合用可讓日期與時間採用同一列。
row 的 items 型別逐列解析品名與金額。任一 locator 可設定 `warning`，
僅當它成功取值時加入該警告，例如現金後備來源的 `total_from_payment`。

region 另提供 `cjk_only: true`，只保留 CJK 字元；內建郵局欄位使用此選項，
以排除混入郵戳區域的數字與英文字。這是 format_version 1 的選用擴充。

anchor 方向：`right`、`below`、`right_or_below`、`left`、`left_or_right`、
`above`、`above_or_below`。左右要求垂直重疊，上下要求水平重疊。
`near` / `not_near` 皆為 `{"labels": ["買受人"], "max_distance": 0.3}`，
依標籤中心的正規化歐氏距離篩選；near 的最近者優先。
`occurrence: "first" | "last"` 控制標籤出現順序，預設從上到下、左到右。
`skip` 跳過包含這些標籤的候選值，`skip_digits: true` 跳過以數字為主的值並繼續搜尋。
`stop_labels` 在最早出現的標籤前截斷值；只有括號、斜線或其他標點時視為 null。

`collect: true` 配合 above/below：收集標籤寬度向左右各擴大 50% 內的最近文字帶，
遵守 max_gap、skip 與 skip_digits，以一個中位行高的間距作為分帶界線，最後按 x 合併。
這可還原圓形印章中分成多框的店名。

table 範例：

```json
{"strategy": "table",
 "columns": {"name": ["品名", "名稱"], "quantity": ["數量"], "unit_price": ["單價"],
             "amount": ["總價", "金額"]},
 "ignore_columns": [["備註"]], "stop_labels": ["合計", "總計"]}
```

表頭逐字估算字元 x 中心，允許字距與破碎 OCR 框；欄標籤用字元子序列匹配，
缺少時可由未佔用的最後一字補足（單價已佔用後，剩餘的「價」可作總價）。
表格左側純 1–2 位數列號、無品名列與刪除線列略過；備註欄文字捨棄。
缺 amount 但有 quantity 與 unit_price 時自動相乘。

## 匹配、校正與輸出

先比對參考圖片：ORB 1500 features、Hamming ratio 0.75、RANSAC，
內點至少 `reference_min_inliers`（預設 25）。成功後將輸入投影到參考圖座標再做 OCR，
使 region 與參考圖一致。沒有參考匹配時，先做可靠的文件校正，再按 OCR 關鍵字匹配。
關鍵字條件為全部 keywords_all 與至少 min_any 個 keywords_any；命中數最多者勝出，
同分時先選使用者模板，再選命中文字較長者。沒有條件的模板只能參考比對或強制套用。
關鍵字同樣忽略空白、全半形、簡繁體與台／臺；少於五字須精確匹配，五字以上才容許一字替換。
這個限制不改變 anchor 欄位標籤的模糊匹配規則。
選用 `match.keywords_none` 是精確排除清單，任何一個命中即略過模板，例如
`"keywords_none": ["高鐵", "車票"]`。排除清單不做模糊匹配。

模板欄位之外的缺少標準欄位由通用解析補上。QR 的權威欄位仍依
[API 優先順序](API.md) 合併。最後共用金額檢查：沒有總額時以可讀品項金額補算，
已有總額但相差超過 0.5 時只警告、不改寫。
品項若合計等於 subtotal，且 subtotal + tax 等於 total，視為未稅品項與含稅總額相符。
`template_field_missing:<key>` 表示模板策略失敗，即使後來通用解析或品項補算取得值也會保留。
頂層 defaults 是格式常數，不產生 template_default_used。

## 新增內建格式

1. 新增 `src/ezreceipt/templates/builtin/<id>/template.json`，不需修改 Python 註冊程式。
2. 以合成 OcrLine 版面加入測試；可另外用合成圖測試 ORB 與真實 OCR。
3. 執行 `template validate`、完整 pytest 及 ruff。
4. 公開版本庫只放合成資料；不要加入真實收據影像、姓名、統編或參考圖。

曲面郵戳、手寫數字與印章重疊仍可能辨識失敗；低分或含警告的結果應人工核對。
內建郵政模板的 post_office 區域為 `[0.76,0.30,0.92,0.40]`，弧形文字經常只能讀到一部分。
