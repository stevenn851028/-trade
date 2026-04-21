# 回測框架

## 原則

1. **回測 = 實盤**：同一套 Strategy + Risk Manager 程式碼，差別只在資料源與執行層
2. **真實成本**：滑價、手續費、期交稅一律計入，寧可保守
3. **無未來資訊**：訊號只能用「當下可得」的資料，嚴禁 look-ahead bias
4. **可重現**：同一份資料 + 同一份程式碼 = 同一份結果（固定隨機種子）
5. **樣本外驗證**：參數決定與績效評估必須分開

## 事件驅動架構

```
for each bar in historical_bars:
    1. emit BarEvent
    2. Strategy.on_bar(bar) → SignalEvent?
    3. RiskManager.on_signal(signal, state) → OrderEvent?
    4. Executor.on_order(order, next_bar_open) → FillEvent
    5. Portfolio.on_fill(fill) → update positions, equity
    6. Recorder.log_everything()
```

與實盤差別：步驟 4 在回測時用「下一根 K 棒開盤價 + 滑價」模擬，實盤由券商回報。

## 交易成本模型

### 手續費
- 單邊 NT$50／口（可設定；各家券商不同）
- 大小台不同：MXF 通常更便宜

### 期交稅
- 每口成交金額 × 0.00002
- = 成交點數 × 200（大台）× 0.00002
- = 成交點數 × 0.004（大台，NT$）

### 滑價
- 市價單：假設 **1 tick**（1 點 = NT$200）滑價
- 可在 config 調整，壓力測試用 2–3 tick

### 範例（大台，進出一趟）
```
假設進場 17,000 點、出場 17,100 點
毛利          = (17,100 - 17,000) × 200                 = NT$20,000
手續費        = 50 × 2 (來回)                          = NT$100
期交稅        = (17,000 + 17,100) × 200 × 0.00002      = NT$136.4
滑價          = 1 × 200 × 2                            = NT$400
淨利          = 20,000 - 100 - 136.4 - 400             = NT$19,363.6
成本占毛利     ≈ 3.2%
```

## 績效指標

### 收益
- **總報酬率**（Total Return）
- **年化報酬率**（CAGR）
- **最終權益曲線**

### 風險
- **最大回撤**（Max Drawdown, MDD）— 權益峰值到谷底的最大跌幅
- **回撤期間**（DD Duration）— 最長未創新高的天數
- **權益波動率**（Annualized Volatility）

### 風險調整後收益
- **Sharpe Ratio** = (年化報酬 − 無風險利率) / 年化波動
- **Sortino Ratio** — 只考慮下行波動
- **Calmar Ratio** = 年化報酬 / 最大回撤

### 交易統計
- **交易次數**
- **勝率**（Win Rate）
- **平均盈虧比**（Avg Win / Avg Loss）
- **期望值**（Expected Value per Trade）
- **盈虧因子**（Profit Factor = 總盈利 / 總虧損）
- **最大連續虧損次數**

### 目標門檻（Phase 1 通過條件）

| 指標 | 目標 |
| --- | --- |
| Sharpe Ratio (年化) | ≥ 1.0 |
| Calmar Ratio | ≥ 0.5 |
| 最大回撤 | ≤ 30% |
| 盈虧因子 | ≥ 1.3 |
| 樣本期間 | ≥ 5 年，涵蓋多頭、空頭、震盪 |

未達標 → 回 Strategy 階段調整。

## 過擬合防範

### 1. 資料切分
```
2018 ─────── 2022    | 2023    | 2024 ─── 2026
 In-sample (訓練)    | Val     | Out-of-sample (最終驗證)
 調參用               | 選模用   | 上線前只跑一次
```

### 2. Walk-Forward Analysis
以滾動窗口方式：
- Window = 2 年訓練 + 6 個月測試
- 每 6 個月重新訓練一次
- 最終將所有測試窗拼起來，產生「準樣本外」績效

### 3. 參數敏感度
- 對 fast/slow 週期做 grid：±20%
- 要求：最佳參數與鄰近參數的績效不應有斷崖式差距
- 若只有單一點表現好 → 過擬合，丟棄

### 4. Monte Carlo 擾動
- 對交易順序隨機洗牌 1000 次 → 分布
- 對成交價格加入隨機 ±0.5 tick 雜訊
- 看 MDD / Sharpe 的 5%–95% 區間

### 5. 禁止事項
- ❌ 跑完全期資料後再調參數
- ❌ 看到績效不好就改策略、重跑同一份資料
- ❌ 用「曾經表現好的參數」作為預設

## 回測輸出

每次回測產生：
1. `equity_curve.csv` — 每根 K 棒的權益值
2. `trades.csv` — 所有進出場記錄
3. `metrics.json` — 所有績效指標
4. `report.html` — 視覺化報告（權益曲線、回撤、月報酬熱力圖、交易分布）
5. `config_snapshot.yaml` — 當次回測的完整設定（可重現）

## 基準（Benchmark）

- **買入持有**：2018 年買進台指並持有至今的報酬
- **定期定額**：每月固定金額買進台指 ETF
- 策略需 **顯著優於** 基準，且風險更低，才有價值

## 執行範例（規劃中 CLI）

```bash
# 跑單次回測
python -m twquant.backtest run \
    --strategy ema_cross_15m \
    --symbol TXF \
    --start 2020-01-01 \
    --end 2024-12-31 \
    --config configs/strategy.yaml \
    --output results/2024_q4_v1

# Walk-forward
python -m twquant.backtest walk_forward \
    --strategy ema_cross_15m \
    --train-window 2y \
    --test-window 6m \
    --start 2018-01-01 \
    --end 2025-12-31
```

## 相關文件

- 策略細節：[`STRATEGY.md`](STRATEGY.md)
- 風控（回測中也會啟用）：[`RISK_MANAGEMENT.md`](RISK_MANAGEMENT.md)
- 資料取得：[`DATA_PIPELINE.md`](DATA_PIPELINE.md)
