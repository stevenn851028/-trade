# TW Quant Trading System（台指期量化交易系統）

> **願景**：建立一套可維運 10–20 年、以技術指標為核心、全自動化的量化交易系統，作為穩健、可複製、不依賴主觀判斷的被動收入來源。

---

## 專案定位

- **時間尺度**：10–20 年，以「累積複利 + 穩定勝率」為目標，不追求短期暴利
- **系統化**：所有進出場、資金配置、風控皆由程式決定並執行，排除情緒干擾
- **可驗證**：策略上線前必須通過 ≥5 年歷史回測、前向分析（walk-forward）與模擬交易
- **可擴充**：從單策略（EMA10/60 交叉）開始，逐步擴充為多策略、多商品、多時間週期的投組
- **可維運**：在本機或 VPS 連續運作，具備斷線重連、狀態恢復、監控告警

## 第一階段策略

| 項目 | 設定 |
| --- | --- |
| 標的 | 台指期貨 TXF（大台）／可選 MXF（小台） |
| K 棒週期 | **15 分鐘（候選 A）** 與 **30 分鐘（候選 B）** — Phase 1 同步回測、擇優上線 |
| 進場訊號 | **EMA10 由下往上貫穿 EMA60**（黃金交叉）→ 做多 |
| 出場訊號 | **EMA10 由上往下貫穿 EMA60**（死亡交叉）→ 平倉 |
| 方向 | 僅做多（Phase 1 暫不做空，之後評估） |
| 部位管理 | 單筆單口，固定口數 |

考量 15 分鐘在台指期交易成本（手續費 + 期交稅）下可能被侵蝕太多，30 分鐘作為成本彈性較大的備選一併驗證。最終以**扣除成本後的 Sharpe / Calmar** 擇優。

詳細規格：[`docs/STRATEGY.md`](docs/STRATEGY.md)

## 文件索引

| 文件 | 目的 |
| --- | --- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 系統整體架構、模組切分、資料流 |
| [`docs/STRATEGY.md`](docs/STRATEGY.md) | EMA10/60 策略完整規格（15m、30m 兩候選）與邊界案例 |
| [`docs/DATA_PIPELINE.md`](docs/DATA_PIPELINE.md) | 行情資料來源、清洗、儲存、連續月合約處理 |
| [`docs/DATA_PIPELINE_FINDINGS.md`](docs/DATA_PIPELINE_FINDINGS.md) | Phase 0.5 pipeline 驗證紀錄、已確認事實、待解盲點 |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | 維運手冊：日常存檔 cron / systemd 設定、故障排查 |
| [`docs/SHIOAJI_SETUP.md`](docs/SHIOAJI_SETUP.md) | 永豐證券開戶 + Shioaji API 設定一次性指南 |
| [`docs/BACKTEST.md`](docs/BACKTEST.md) | 回測框架設計、績效指標、過擬合防範 |
| [`docs/RISK_MANAGEMENT.md`](docs/RISK_MANAGEMENT.md) | 部位大小、停損、資金管理、Kill Switch |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Phase 0 → Phase 5 分期發展計畫 |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | 開發規範、分支策略、測試要求、部署流程 |

## 專案進度

**目前階段：Phase 0 — 系統設計（進行中）**

尚未進入實作階段。所有架構文件完成並經過檢討後，才會開始撰寫程式碼。

見 [`docs/ROADMAP.md`](docs/ROADMAP.md)。

## 原型程式

[`prototypes/taiwan_index_ema.py`](prototypes/taiwan_index_ema.py) 是初期的快速驗證腳本，僅供概念參考，**不會進入正式系統**。正式實作將依據 `docs/ARCHITECTURE.md` 的模組切分重新撰寫。

## 技術棧（暫定）

| 層級 | 選型 |
| --- | --- |
| 語言 | Python 3.11+ |
| 資料處理 | pandas / polars |
| 回測引擎 | 自研事件驅動引擎（或評估 backtrader / vectorbt） |
| 券商 API | 永豐 Shioaji（優先）／群益、凱基 備選 |
| 儲存 | SQLite（Phase 1）→ PostgreSQL + TimescaleDB（Phase 3+） |
| 排程 | APScheduler / systemd timer |
| 監控 | loguru + Telegram / Line Notify |
| 部署 | 本機 → VPS（Docker） |

詳見 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 免責聲明

本專案為個人研究用途，所有內容與程式碼不構成任何投資建議。期貨交易具有槓桿風險，可能造成超過原始投入的虧損，實際執行前請務必自行評估風險並諮詢專業人士。
