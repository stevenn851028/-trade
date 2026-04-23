# 資料管線

## 目標

提供系統一份 **乾淨、連續、無跳空、時區正確** 的台指期 K 棒資料，供回測與實盤使用。Phase 1 同時生產 **15 分鐘** 與 **30 分鐘** 兩個週期（兩候選都要跑）；以 1 分 K 為單一真相來源（single source of truth）、向上聚合。

## 資料型別

### 1. Tick / 逐筆成交
- 欄位：`datetime, price, volume, bid, ask`
- 用途：實盤聚合為 K 棒、盤中監控
- 來源：Shioaji 即時 quote

### 2. K 棒（OHLCV）
- 欄位：`datetime, open, high, low, close, volume, oi`（未平倉量）
- 週期：1m / 5m / **15m** / **30m** / 1h / 1d
- 用途：回測與策略主要輸入
- **15m 與 30m 一律由 1m K 棒聚合產生**（避免不同來源定義不一致，確保 30m = 兩根 15m）

### 3. 契約資訊
- 欄位：`symbol, month, last_trading_date, settle_date, tick_size, contract_size, margin`
- 用途：轉倉判斷、保證金計算

## 資料來源

| 來源 | 類型 | 覆蓋範圍 | 費用 | 備註 |
| --- | --- | --- | --- | --- |
| **TAIFEX 官網** | 日成交檔 CSV | **公開下載僅最近 ~30 個交易日** | 免費 | 盤後公告，最可靠的「官方真實價格」；須立即啟用日常存檔 |
| **TAIFEX 正式申請** | 歷史逐筆資料 | 2000+ 至今 | 付費／申請 | 完整長期歷史唯一官方來源 |
| **Shioaji（永豐）** | 歷史 K 棒 API + 即時 tick | 近 2 年 K 棒 | 需開戶 | 實盤主力，也可補歷史 |
| **yfinance** | 歷史日線 / 分鐘線 | 最多 60 天分鐘 | 免費 | 僅原型使用，不適合長期回測 |
| **FinMind / TEJ** | 第三方整合 | 多元 | 免費 / 付費 | 備選，資料品質需以 TAIFEX 黃金標準抽檢 |

> ⚠️ **官方 30 天限制**：TAIFEX 公開下載只保留近 30 交易日。長期回測資料必須 **立即開始日常存檔**，或另購／申請付費歷史資料。詳見 [`DATA_PIPELINE_FINDINGS.md`](DATA_PIPELINE_FINDINGS.md)。

**策略**：
- **歷史 1 分 K**：TAIFEX 逐筆 CSV 聚合（最可信） + Shioaji / 第三方補更早期間
- **即時**：Shioaji quote subscribe
- **日常存檔**：每交易日 14:30 / 06:30 自動抓 Daily ZIP 落地，避免 30 天外滾失
- 兩邊對齊後落地，任何不一致寫入 `data_quality.log`

## 連續月合約（Continuous Contract）

台指期每月到期換倉，歷史回測需串接成連續序列。

### 轉倉規則（採用 Panama 方法 + 成交量觸發）
1. **觸發日**：每月第三個星期三（結算日）的**前一交易日收盤後**
2. **判斷**：次月合約當日成交量 > 當月合約 → 切換
3. **接價調整**：以切換當日兩合約收盤價差為 offset，調整歷史價格（減去 offset），避免跳空
4. **記錄**：所有 offset 與切換日期寫入 `rollover.log`

### 備註
- 策略面看到的是「連續合約」，不會感受到換月
- 實盤下單時由 Executor 選擇當月主力月（成交量最大者）

## 時區

- 統一使用 **Asia/Taipei（UTC+8）**
- 資料庫內以 UTC 儲存，應用層轉為 Taipei
- 夜盤跨日的 K 棒：`datetime` 採用 K 棒**結束時間**

## K 棒邊界（15m / 30m）

台指日盤 08:45 開盤，夜盤 15:00 開始。切分方式：

| 時段 | 15m 切分 | 30m 切分 |
| --- | --- | --- |
| 日盤（08:45–13:45） | 08:45-09:00 / 09:00-09:15 / ... / 13:30-13:45（20 根） | 08:45-09:15 / 09:15-09:45 / ... / 13:15-13:45（10 根） |
| 夜盤（15:00–05:00） | 15:00-15:15 / ... / 04:45-05:00（56 根） | 15:00-15:30 / ... / 04:30-05:00（28 根） |

**重要**：
- 兩個週期的第一根日盤 K 棒皆自 08:45 起（非對齊整點）；若資料源以 09:00 為邊界需重新聚合
- 30m 的每一根皆為連續兩根 15m 的聚合（確保兩者在同一時點對齊）

## 資料品質檢查

每次 ETL 完成後自動執行（以 15m 為主、30m 同步檢查）：

| 檢查項 | 規則（15m / 30m） | 動作 |
| --- | --- | --- |
| K 棒缺漏 | 15m：預期 76 根/日，< 72；30m：預期 38 根/日，< 36 | 記錄 + 告警 |
| OHLC 邏輯 | `low ≤ open, close ≤ high` | 修正或剔除 |
| 跳空 | `abs(open[t] - close[t-1]) / close[t-1] > 3%` | 標記，不自動修 |
| 成交量異常 | 單根 K 棒成交量 > 30 日均值 × 10 | 標記 |
| 時間序列 | 無重複、無亂序 | 自動排序與去重 |
| 休市日 | 對照 TAIFEX 行事曆 | 休市日不應有資料 |

## 儲存設計

### Phase 1：SQLite

使用單張通用表，欄位 `tf` 區分週期，避免未來每新增週期就開新表：

```sql
CREATE TABLE bars (
    symbol      TEXT    NOT NULL,        -- TXF / MXF
    tf          TEXT    NOT NULL,        -- '1m' / '15m' / '30m' / '1d'
    month_code  TEXT,                    -- 202606 等，連續合約為 'CONT'
    ts          INTEGER NOT NULL,        -- K 棒結束時間，epoch seconds (UTC)
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      INTEGER NOT NULL,
    oi          INTEGER,
    PRIMARY KEY (symbol, tf, month_code, ts)
);
CREATE INDEX idx_bars_tf_ts ON bars(tf, symbol, ts);
```

查詢範例：
```sql
-- 15m 候選
SELECT * FROM bars WHERE tf = '15m' AND symbol = 'TXF' AND month_code = 'CONT';
-- 30m 候選
SELECT * FROM bars WHERE tf = '30m' AND symbol = 'TXF' AND month_code = 'CONT';
```

### Phase 3+：PostgreSQL + TimescaleDB
- `bars` 轉為 hypertable，依 `ts` 自動分區；`tf` 作為分區外索引鍵
- Tick 資料獨立表（可選）

## ETL 流程

```
┌─────────────────┐
│ TAIFEX CSV      │─┐
└─────────────────┘ │
                    ▼
┌─────────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────┐
│ Shioaji 歷史 API│─▶  Normalize │─▶ Validate  │─▶ Rollover │─▶ SQLite
└─────────────────┘ └────────────┘ └────────────┘ └──────────┘
                         ▲
                         │
┌─────────────────┐      │
│ Shioaji 即時    │──────┘ (tick → 1m → 15m / 30m 即時聚合)
└─────────────────┘
```

## 回補（Backfill）

- 首次部署：回補最近 5 年 1 分 K
- 日常：每日盤後 14:00 自動回補當日日盤、次日 06:00 回補夜盤
- 任何中斷：記錄中斷區間，次日自動比對並重跑該區間

## 不會做的事

- **不做基本面資料**（財報、總經）：本系統純技術面
- **不做第三方回測平台 API**（如 Quantopian）：自主掌控
- **不做跨市場套利資料**：專注台指期

## 相關文件

- 契約規格詳見 [`STRATEGY.md`](STRATEGY.md)
- 交易成本計算見 [`BACKTEST.md`](BACKTEST.md)
