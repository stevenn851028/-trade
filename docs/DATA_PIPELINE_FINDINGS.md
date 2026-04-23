# 資料管線驗證紀錄（Phase 0.5）

> 本文件記錄 **2026-04-22** 實作 TAIFEX 資料管線時所發現的事實、假設、未解問題，以及原 `DATA_PIPELINE.md` 設計需要補充的地方。

## 實作範圍

已完成的模組（`src/twquant/data/`）：

| 檔案 | 職責 | 測試 |
| --- | --- | --- |
| `session.py` | 台北日／夜盤時段、bar 結束時間計算 | `test_session.py`（28 cases） |
| `tick_parser.py` | 解析 TAIFEX Big5 CSV → 正規化 DataFrame | `test_tick_parser.py`（8 cases） |
| `bar_aggregator.py` | Ticks → OHLCV bars；支援 1m → 15m / 30m 向上聚合 | `test_bar_aggregator.py`（7 cases） |
| `quality.py` | K 棒缺漏、OHLC 邏輯、跳空、重複等檢查 | `test_quality.py`（5 cases） |
| `taifex_downloader.py` | 下載 `Daily_YYYY_MM_DD.zip`（含 retry、cache） | *CLI smoke test* |
| `cli.py` | `fetch` / `verify` 子命令 | *CLI smoke test* |
| *End-to-end* | 合成 TAIFEX ZIP → 完整 pipeline → 品質報告 | `test_integration_pipeline.py` |

**測試結果：49 / 49 通過**。

## 已確認的事實（Confirmed）

1. **下載 URL 格式**：
   ```
   https://www.taifex.com.tw/file/taifex/Dailydownload/DailydownloadCSV/Daily_YYYY_MM_DD.zip
   ```
   對應 options 版本的 URL 是 `OptionsDailydownloadCSV/OptionsDaily_YYYY_MM_DD.zip`
2. **官方可取得的區間**：**最近約 30 個交易日**，更早資料需向 TAIFEX 正式申請
3. **Daily ZIP 內容**：單一 CSV，欄位順序：
   ```
   成交日期, 商品代號, 到期月份(週別), 成交時間, 成交價格,
   成交數量(B+S), 近月價格, 遠月價格, 開盤集合競價
   ```
4. **檔案編碼**：**Big5**（舊檔；社群回報部分新檔可能為 UTF-8，本 parser 以 `--encoding` 參數支援切換）
5. **時間欄位格式**：`HHMMSS` 或 `HHMMSSfff`（帶毫秒）皆須支援
6. **日期欄位格式**：`YYYYMMDD`
7. **商品代號**：`TX`（大台）、`MTX`（小台）、`TE`、`TF`...；parser 以白名單方式過濾
8. **開盤集合競價**：單欄位 `*` 標記；非開盤 tick 為空字串
9. **台指期 Session 時間**（Taipei）：
   - 日盤 08:45 – 13:45
   - 夜盤 15:00 – 次日 05:00（跨日）
10. **15m / 30m 每日 bar 數**：
    - 日盤 15m 20 根、30m 10 根
    - 夜盤 15m 56 根、30m 28 根
    - 合計 15m **76**、30m **38**（與 `DATA_PIPELINE.md` 原設計一致，測試已反向驗證）

## 設計決策 & 重要細節

### Bar 區間約定：`(start, end]`，ts 以 bar 結束時間為 canonical label

- 08:45 的開盤 tick → 歸屬於 bar ending at 09:00（15m）或 09:15（30m）
- 實作於 `session.bar_end()`，測試涵蓋開盤、收盤、跨分鐘邊界

### 日盤第一根 bar 為 **(08:45, 09:00]**（非對齊整點）

- 許多資料源預設邊界在 09:00，若未修正會得到錯位的 15m bars
- 本 parser 以 session-anchored bucketing 處理，**不依賴** 整點對齊

### 30m 與 15m 的一致性（重要）

- 30m 的每根 = 兩根 15m 的聚合
- 測試 `test_30m_is_equivalent_to_rollup_of_15m` 明確保證此性質
- 未來改用 1m 為 single source of truth 時，此性質不變

### 連續月合約（rollover）

- 目前僅實作「當日成交量最大者為主力月」（`pick_dominant_month()`）
- **尚未實作 Panama-style 價差調整**（設計於 `DATA_PIPELINE.md` § 連續月合約）
- 回測前必須補上，否則每月換月日會出現假跳空

## 新發現、原 DATA_PIPELINE.md 未涵蓋的盲點

以下是本次實作中浮現、值得寫回設計文件或需追蹤的點：

### 1. 官方僅保留 30 天 → 需要立即開始「日常存檔」排程

**影響**：若想回測 2018–2025 完整 5 年資料，**不能光靠官方網站爬**。可行方案：
- 立即開始每日排程下載、本地永久保存（越早越好）
- 向 TAIFEX 正式申請歷史資料（可能收費）
- 透過第三方（FinMind、TEJ）補歷史，再與最近 30 天 TAIFEX 對帳確認品質

**建議**：Phase 0.6 立即啟用日常 cron，先累積。

### 2. `近月價格` / `遠月價格` 兩欄在單一月合約沒有資料

- 正常單月合約成交這兩欄為空字串
- 這兩欄只對「價差（spread）交易」才有值
- Parser 目前略過這兩欄；若將來要做價差策略需回頭處理

### 3. 開盤集合競價 tick 的 timestamp 是集合競價撮合瞬間

- 08:44:xx 左右；我們的 `bar_end(08:45:00, 15) == 09:00` 正確涵蓋
- 但若 timestamp 為 08:44:59 則 `classify()` 會回傳 None（非日盤時段）
- **驗證清單**：實際檔案中 `is_opening_auction=*` 的 tick 時間範圍需 sample 確認

### 4. 休市日 / 颱風假 → HTTP 404 是正常的

- Downloader 將 404 標記為 `not_found`（非 error），不重試、不告警
- 週末自動跳過（`weekday() < 5`）
- 國定假日仍會嘗試下載並得到 404；這是可接受的行為

### 5. 夜盤跨日的日期歸屬

- 凌晨 00:00 – 05:00 的 tick 寫在「當晚開始日」的 Daily ZIP 中
- 例：4/15 夜盤開盤到 4/16 05:00 的 tick，都在 `Daily_2026_04_15.zip`
- 經實務確認，但**本結論為推斷**，上線前需以實際 TAIFEX 檔案驗證

### 6. SQLite 實際儲存還未接上

- 目前 CLI `--out-dir` 只輸出 per-day CSV
- 下一步應補 `sqlite_store.py`：upsert bars、跨日合併查詢介面
- schema 依 `DATA_PIPELINE.md`：`bars(symbol, tf, month_code, ts, open, high, low, close, volume)` 單表設計

### 7. Tick 資料不保留（目前行為）

- Pipeline 收到 tick → 當場聚合成 bar → 丟棄 tick
- 若將來要做極短線、tick-by-tick 回測則需另存
- 空間考量：一日 TX 約 100MB–300MB raw CSV；5 年壓縮後約 50 GB 量級

## 你需要在本機跑的一次性驗證

此 sandbox 無法外連 taifex.com.tw。**請你在本機**（有網路）執行以下流程，確認真實資料與本管線假設相符：

```bash
# 1. 裝相依
pip install -r requirements-dev.txt

# 2. 跑測試（應 49 passed）
PYTHONPATH=src pytest

# 3. 下載最近幾個交易日的 ZIP
PYTHONPATH=src python -m twquant.data.cli fetch \
    --start 2026-04-15 --end 2026-04-22 \
    --out-dir data/raw

# 4. 解析、聚合、品檢報告（預設 15m + 30m）
PYTHONPATH=src python -m twquant.data.cli verify \
    --start 2026-04-15 --end 2026-04-22 \
    --raw-dir data/raw \
    --out-dir data/bars
```

### 請回報以下資訊以修正設計盲點

1. **實際下載到的 ZIP 大小**（單日 TX 預估 5–30 MB）
2. **`verify` 報告的 `bars=X/76` 與 `bars=X/38`** — 若有偏差回報
3. **是否出現 PARSE FAIL** — 若 Big5 解碼失敗，試 `--encoding utf-8`
4. **開盤集合競價 tick 的實際時間** — 比對 08:44:xx vs 08:45:00
5. **休市日 404 是否全部被正確識別** — 不應計入 error

## 下一步 TODO

- [ ] **立即**：在本機啟動每日 cron 存檔 `Daily_*.zip`（避免 30 天外滾失）
- [ ] 補 Panama-style continuous contract 合約串接
- [ ] 補 `sqlite_store.py` 與 `bars` 單表 upsert
- [ ] 確認夜盤跨日資料的歸屬（實際 TAIFEX 檔案驗證）
- [ ] 聯繫 TAIFEX / 評估付費歷史資料申請流程
- [ ] 進入 Phase 1：Strategy engine 接上 bar stream

## 對 `DATA_PIPELINE.md` 的建議更新

在 `§ 資料來源` 表格底下加一列強調：

> ⚠️ **官方 30 天限制**：TAIFEX 公開下載只保留近 30 交易日。長期回測資料必須立即開始日常存檔，或另購付費歷史資料。

在 `§ ETL 流程` 後補一段 `§ 30 天滾動保存` 子節，描述每日 cron 的設計。

這些更新在下次 review 時一起納入 `DATA_PIPELINE.md`。
