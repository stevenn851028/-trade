# 資料管線

## 目標

提供系統一份 **乾淨、連續、無跳空、時區正確** 的台指期 30 分鐘 K 棒資料，供回測與實盤使用。

## 資料型別

### 1. Tick / 逐筆成交
- 欄位：`datetime, price, volume, bid, ask`
- 用途：實盤聚合為 K 棒、盤中監控
- 來源：Shioaji 即時 quote

### 2. K 棒（OHLCV）
- 欄位：`datetime, open, high, low, close, volume, oi`（未平倉量）
- 週期：1m / 5m / 15m / **30m** / 1h / 1d
- 用途：回測與策略主要輸入
- 30m K 棒由 1m K 棒聚合產生（避免不同來源對 30m 定義不一致）

### 3. 契約資訊
- 欄位：`symbol, month, last_trading_date, settle_date, tick_size, contract_size, margin`
- 用途：轉倉判斷、保證金計算

## 資料來源

| 來源 | 類型 | 覆蓋範圍 | 費用 | 備註 |
| --- | --- | --- | --- | --- |
| **TAIFEX 官網** | 日成交檔 CSV | 2000+ 至今 | 免費 | 盤後公告，最可靠的「官方真實價格」，建議作為回測黃金標準 |
| **Shioaji（永豐）** | 歷史 K 棒 API + 即時 tick | 近 2 年 K 棒 | 需開戶 | 實盤主力，也可補歷史 |
| **yfinance** | 歷史日線 / 分鐘線 | 最多 60 天分鐘 | 免費 | 僅原型使用，不適合長期回測 |
| **FinMind / TEJ** | 第三方整合 | 多元 | 免費 / 付費 | 備選，資料品質需驗證 |

**策略**：
- **歷史 1 分 K**：TAIFEX 逐筆 CSV 聚合（最可信） + Shioaji 補近期
- **即時**：Shioaji quote subscribe
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

## 30 分鐘 K 棒邊界

台指日盤 08:45 開盤，夜盤 15:00 開始。30 分鐘切分方式：

| 時段 | 切分方式 | 範例 |
| --- | --- | --- |
| 日盤 | 從 08:45 起每 30 分鐘一根 | 08:45-09:15 / 09:15-09:45 / ... / 13:15-13:45 |
| 夜盤 | 從 15:00 起每 30 分鐘一根 | 15:00-15:30 / 15:30-16:00 / ... / 04:30-05:00 |

**重要**：第一根日盤 K 棒是 08:45–09:15（30 分鐘），若資料源切成 09:00 邊界需重新聚合。

## 資料品質檢查

每次 ETL 完成後自動執行：

| 檢查項 | 規則 | 動作 |
| --- | --- | --- |
| K 棒缺漏 | 預期 38 根/交易日，實際 < 36 | 記錄 + 告警 |
| OHLC 邏輯 | `low ≤ open, close ≤ high` | 修正或剔除 |
| 跳空 | `abs(open[t] - close[t-1]) / close[t-1] > 3%` | 標記，不自動修 |
| 成交量異常 | 單根 K 棒成交量 > 30 日均值 × 10 | 標記 |
| 時間序列 | 無重複、無亂序 | 自動排序與去重 |
| 休市日 | 對照 TAIFEX 行事曆 | 休市日不應有資料 |

## 儲存設計

### Phase 1：SQLite
```sql
CREATE TABLE bars_30m (
    symbol      TEXT    NOT NULL,        -- TXF / MXF
    month_code  TEXT,                    -- 202606 等，連續合約為 'CONT'
    ts          INTEGER NOT NULL,        -- K 棒結束時間，epoch seconds (UTC)
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      INTEGER NOT NULL,
    oi          INTEGER,
    PRIMARY KEY (symbol, month_code, ts)
);
CREATE INDEX idx_bars_symbol_ts ON bars_30m(symbol, ts);
```

### Phase 3+：PostgreSQL + TimescaleDB
- `bars_30m` 轉為 hypertable，依時間自動分區
- 1m bars 獨立表
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
│ Shioaji 即時    │──────┘ (tick → 1m → 30m 即時聚合)
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
