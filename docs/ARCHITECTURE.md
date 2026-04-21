# 系統架構

## 設計原則

1. **模組解耦**：資料、策略、風控、執行彼此獨立，透過明確介面通訊，方便單獨測試與替換
2. **事件驅動**：採 event-driven 架構，使同一套程式可用於回測、模擬、實盤，避免「回測漂亮、實盤失真」
3. **狀態可持久化**：任何時刻重啟都能從上次狀態續跑，不會漏訊號或重複下單
4. **失敗安全**：斷線、API 錯誤、異常部位，一律進入保守模式並通知
5. **可觀測性**：所有訊號、訂單、成交、部位變化皆寫入持久化 log，可追溯與重建

## 高階架構圖

```
┌─────────────────────────────────────────────────────────────────┐
│                         TW Quant System                          │
└─────────────────────────────────────────────────────────────────┘

 ┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
 │ Data Source  │───▶│ Data Pipeline│───▶│  Market Data Bus  │
 │ (Shioaji,    │     │ (normalize,  │     │  (bar events,     │
 │  yfinance,   │     │  clean,      │     │   tick events)    │
 │  TAIFEX CSV) │     │  rollover)   │     │                   │
 └──────────────┘     └──────────────┘     └─────────┬─────────┘
                                                     │
                                                     ▼
                             ┌───────────────────────────────────┐
                             │      Strategy Engine              │
                             │  (EMA10/60 crossover, 30m)        │
                             │  → SignalEvent(LONG / FLAT)       │
                             └─────────┬─────────────────────────┘
                                       │
                                       ▼
                             ┌───────────────────────────────────┐
                             │      Risk Manager                 │
                             │  - position sizing                │
                             │  - max drawdown guard             │
                             │  - kill switch                    │
                             │  → OrderEvent                     │
                             └─────────┬─────────────────────────┘
                                       │
                     ┌─────────────────┴─────────────────┐
                     ▼                                   ▼
        ┌──────────────────────┐          ┌──────────────────────┐
        │  Backtest Executor   │          │  Live Executor       │
        │  (simulated fills,   │          │  (Shioaji order API, │
        │   slippage, fees)    │          │   fill callback)     │
        └──────────┬───────────┘          └──────────┬───────────┘
                   │                                 │
                   └────────────────┬────────────────┘
                                    ▼
                         ┌────────────────────────┐
                         │  Portfolio / State     │
                         │  (positions, equity,   │
                         │   trade log, metrics)  │
                         └──────────┬─────────────┘
                                    ▼
                         ┌────────────────────────┐
                         │  Observability         │
                         │  (logs, DB, Telegram)  │
                         └────────────────────────┘
```

## 模組責任

### 1. Data Source（資料源）
- 歷史資料：TAIFEX 每日盤後下載、券商歷史 API
- 即時行情：Shioaji `quote.subscribe`（tick / bid-ask）
- 合約資訊：契約規格、保證金、結算日

### 2. Data Pipeline（資料處理）
- Tick → 30 分鐘 K 棒聚合
- 連續月合約串接（rollover）
- 時區正規化（Asia/Taipei）
- 資料完整性檢查（缺漏、重複、異常值）
- 落地到 SQLite / Parquet

### 3. Market Data Bus（事件匯流）
- 統一事件型別：`BarEvent`, `TickEvent`, `OrderEvent`, `FillEvent`, `SignalEvent`
- 回測模式：由 pipeline 按時間序列 replay
- 實盤模式：由 Shioaji callback 推入

### 4. Strategy Engine（策略引擎）
- 輸入：`BarEvent`
- 維護：EMA10、EMA60 狀態
- 輸出：`SignalEvent(target_position: LONG | FLAT)`
- 只負責「應該持有什麼部位」，不負責「下幾口、何時下」

### 5. Risk Manager（風控）
- 將 `SignalEvent` 轉為 `OrderEvent`
- 部位大小：依據資金、波動率、保證金計算口數
- 停損、停利：硬性上限
- Kill Switch：累計虧損達閾值 → 停止新進場、只准平倉
- 資金使用率上限（margin utilization cap）

### 6. Executor（執行層）
- **Backtest Executor**：模擬成交，加入滑價、手續費、期交稅
- **Live Executor**：呼叫券商 API 下單，處理成交、拒單、部分成交
- 兩者實作同一介面，策略層無感切換

### 7. Portfolio / State（部位與狀態）
- 即時部位、均價、未實現損益
- 已實現損益、權益曲線
- 交易紀錄（每筆進出場時間、價格、原因、損益）
- 狀態持久化：SQLite + periodic snapshot

### 8. Observability（可觀測性）
- 結構化日誌（loguru + JSON）
- 關鍵事件推播：Telegram / Line Notify
- Dashboard（Phase 3+）：Grafana / 自製 web

## 執行模式

| 模式 | 目的 | 資料來源 | 執行層 |
| --- | --- | --- | --- |
| **Backtest** | 歷史驗證 | 歷史 K 棒（DB） | 模擬撮合 |
| **Paper Trading** | 真實市場驗證 | 即時行情 | 模擬撮合 |
| **Live** | 真金白銀 | 即時行情 | 券商 API |

三種模式共用 Strategy + Risk Manager 程式碼，僅切換資料源與執行層。

## 專案目錄結構（規劃）

```
-trade/
├── README.md
├── docs/
│   ├── ARCHITECTURE.md
│   ├── STRATEGY.md
│   ├── DATA_PIPELINE.md
│   ├── BACKTEST.md
│   ├── RISK_MANAGEMENT.md
│   ├── ROADMAP.md
│   └── DEVELOPMENT.md
├── prototypes/                 # 概念驗證用的快速腳本
├── src/
│   ├── twquant/
│   │   ├── __init__.py
│   │   ├── events.py           # 事件型別定義
│   │   ├── data/               # Data Source + Pipeline
│   │   ├── strategies/         # 策略實作
│   │   │   └── ema_crossover.py
│   │   ├── risk/               # Risk Manager
│   │   ├── execution/          # Backtest / Live Executor
│   │   ├── portfolio/          # 部位與狀態
│   │   ├── observability/      # log, notify
│   │   └── app/                # 進入點（backtest / paper / live）
├── tests/
│   ├── unit/
│   └── integration/
├── configs/
│   ├── strategy.yaml
│   ├── risk.yaml
│   └── broker.yaml
├── data/                       # 本機資料快取（.gitignore）
├── logs/                       # 執行日誌（.gitignore）
├── notebooks/                  # 研究用 Jupyter
├── pyproject.toml
└── requirements.txt
```

## 技術選型說明

### 為何自研事件驅動引擎？
- 市面上的 `backtrader` 對台股期貨不友善、社群更新慢
- `vectorbt` 速度快但難以在實盤使用同一套邏輯
- 自研引擎程式碼量不大（< 2000 行），換來回測 / 實盤邏輯完全一致

### 為何選 Shioaji？
- 永豐金證券官方 Python SDK，台指期支援完整
- 免費、文件完整、社群活躍
- 備選：群益 API、凱基 API

### 儲存策略
- **Phase 1–2**：SQLite（單檔、無需伺服器）
- **Phase 3+**：當資料量 > 5 GB 或需要多程序並行時，遷移至 PostgreSQL + TimescaleDB

## 相關文件

- 策略細節：[`STRATEGY.md`](STRATEGY.md)
- 資料處理細節：[`DATA_PIPELINE.md`](DATA_PIPELINE.md)
- 回測引擎規格：[`BACKTEST.md`](BACKTEST.md)
- 風控規則：[`RISK_MANAGEMENT.md`](RISK_MANAGEMENT.md)
