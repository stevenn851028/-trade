# 策略規格：EMA10/60 Crossover (30-Minute)

## 概要

一條短期 EMA（EMA10）與一條中長期 EMA（EMA60）的交叉策略，應用於台指期貨 30 分鐘 K 棒。設計哲學：**順勢、慢動作、少交易**。

## 基本資訊

| 項目 | 值 |
| --- | --- |
| 策略代號 | `ema_cross_30m_v1` |
| 標的 | 台指期貨（TXF 大台 / MXF 小台） |
| K 棒週期 | 30 分鐘 |
| 快線 | EMA10（5 小時記憶） |
| 慢線 | EMA60（30 小時記憶，約 1.5 個交易日） |
| 允許方向 | 僅做多（Phase 1） |
| 最大同時部位 | 1 口 |

## 訊號定義

### 進場（Entry）

當前 K 棒收盤後判斷：
```
條件：EMA10[t-1] <= EMA60[t-1]  AND  EMA10[t] > EMA60[t]
動作：於下一根 K 棒開盤市價買進 1 口
```

### 出場（Exit）

當前 K 棒收盤後判斷：
```
條件：EMA10[t-1] >= EMA60[t-1]  AND  EMA10[t] < EMA60[t]
動作：於下一根 K 棒開盤市價平倉
```

### 重點
- 訊號在 **K 棒收盤才確認**，避免盤中振盪造成的假訊號
- 下單在 **下一根 K 棒開盤**，確保回測與實盤邏輯一致
- 快慢線「相等」時不觸發訊號（避免浮點誤差導致連續觸發）

## EMA 計算

```
EMA[t] = α × Close[t] + (1 − α) × EMA[t-1]
其中 α = 2 / (N + 1)
```

- 初始值：使用前 N 根 K 棒的 SMA 作為種子
- 歷史資料需要 **至少暖機 60 根**（約 2 個交易日）才產出第一個有效訊號

## 交易時段

台指期有日盤 + 夜盤：

| 時段 | 時間（台北） | 30 分鐘 K 棒數 |
| --- | --- | --- |
| 日盤 | 08:45 – 13:45 | ~10 根 |
| 夜盤 | 15:00 – 次日 05:00 | ~28 根 |
| 合計（每交易日） | — | ~38 根 |

**設計決策（需於實作前確認）**：

- **方案 A（預設）**：日盤 + 夜盤連續視為同一時間序列，EMA 跨時段連續計算
- **方案 B**：只交易日盤，夜盤不計 EMA、不下單
- **方案 C**：日夜盤分別計算 EMA（兩套狀態）

**傾向 A**：訊號密度高、趨勢延續性佳；但需注意夜盤流動性與假期跳空。

## 邊界案例（Edge Cases）

| 情境 | 處理方式 |
| --- | --- |
| 系統啟動時剛好在交叉中 | 以當下 EMA 關係判斷應持有部位，若實際部位不符則調整（對齊）。首次啟動不補歷史訊號 |
| 跳空開盤穿越 EMA | 仍於下一根開盤執行，不追價 |
| 收盤 K 棒缺漏（行情中斷） | 跳過該根，EMA 不更新；中斷 > 3 根進入保守模式 |
| 結算日當日 | 最後交易日 13:30 前平倉並禁止新進場（詳見 Risk） |
| 契約轉倉 | Data Pipeline 自動串接連續月合約；策略不感知轉倉 |
| 漲跌停鎖死 | 訊號產生但無法成交 → 記錄並在下一根重試 |
| 保證金不足 | Risk Manager 攔截，不下單並告警 |

## 偽碼（Pseudocode）

```python
class EmaCross30m:
    def __init__(self, fast=10, slow=60):
        self.ema_fast = EMA(fast)
        self.ema_slow = EMA(slow)
        self.prev_fast = None
        self.prev_slow = None
        self.position = FLAT

    def on_bar(self, bar: Bar) -> Signal | None:
        self.ema_fast.update(bar.close)
        self.ema_slow.update(bar.close)

        if not (self.ema_fast.ready and self.ema_slow.ready):
            return None

        f, s = self.ema_fast.value, self.ema_slow.value
        signal = None

        if self.prev_fast is not None:
            crossed_up   = self.prev_fast <= self.prev_slow and f > s
            crossed_down = self.prev_fast >= self.prev_slow and f < s

            if crossed_up and self.position == FLAT:
                signal = Signal(target=LONG, reason="golden_cross")
            elif crossed_down and self.position == LONG:
                signal = Signal(target=FLAT, reason="death_cross")

        self.prev_fast, self.prev_slow = f, s
        return signal
```

## 參數

| 參數 | 預設 | 可調範圍 | 備註 |
| --- | --- | --- | --- |
| `fast_period` | 10 | 5–20 | Phase 1 固定為 10 |
| `slow_period` | 60 | 30–120 | Phase 1 固定為 60 |
| `session_mode` | `combined` | `combined` / `day_only` / `split` | 日夜盤處理方式 |
| `warmup_bars` | 60 | ≥ slow_period | 暖機 K 棒數 |

參數調整須重新跑完整回測 + walk-forward，避免過擬合（見 [`BACKTEST.md`](BACKTEST.md)）。

## 預期特性（待回測驗證）

以此類趨勢跟隨策略的先驗（prior）估計：

- **勝率**：40–50%（低勝率）
- **損益比**：1.5 – 2.5（贏的時候多）
- **月均交易次數**：5–15 次
- **最大回撤**：15–30%（需以風控降低）
- **年化報酬**：目標 10–20%（扣除成本後）

> ⚠️ 以上為類似策略的歷史統計，本策略實際績效以回測為準。若顯著偏離此範圍，應重新審視資料或邏輯是否有錯。

## 退役準則（Deactivation Criteria）

策略上線後若觸發以下任一條件，強制停用並回 Phase 1 review：

- 連續 6 個月負報酬
- 最大回撤 > 40%
- 30 個交易日勝率 < 20%
- 績效與回測統計顯著偏離（t-test p < 0.01）

## 下一步

實作前需確認：
1. 日夜盤處理方式（方案 A/B/C）
2. 是否加入做空（對稱反向邏輯）
3. 是否加入確認濾網（如成交量、ATR、更長週期 EMA）
