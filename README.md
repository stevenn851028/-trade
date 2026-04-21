# 台指期 EMA 量化指標

以 EMA（指數移動平均）為核心的台指期／台灣加權指數量化分析腳本：抓取歷史資料、計算多條 EMA、偵測黃金／死亡交叉並執行簡易回測。

## 安裝

```bash
pip install -r requirements.txt
```

## 使用

```bash
# 預設抓取台灣加權指數、EMA12/26 交叉策略
python taiwan_index_ema.py

# 改用台指期連續近月合約
python taiwan_index_ema.py --symbol TX=F --start 2023-01-01

# 自訂 EMA 週期並輸出 CSV
python taiwan_index_ema.py --fast 5 --slow 20 --long 60 --output txf_ema.csv
```

## 參數

| 參數 | 說明 | 預設 |
| --- | --- | --- |
| `--symbol` | Yahoo Finance 代號（`^TWII` 加權、`TX=F` 台指期） | `^TWII` |
| `--start` / `--end` | 日期區間 `YYYY-MM-DD` | 2020-01-01 ~ 今日 |
| `--interval` | K 棒週期（`1d`, `1h`, `15m` …） | `1d` |
| `--fast` / `--slow` / `--long` | EMA 週期 | 12 / 26 / 60 |
| `--capital` | 回測起始資金 | 1,000,000 |
| `--output` | 輸出含指標的 CSV 路徑 | 無 |

## 策略邏輯

- **進場**：快線 EMA 由下往上穿越慢線 EMA（黃金交叉）→ 做多
- **出場**：快線 EMA 由上往下穿越慢線 EMA（死亡交叉）→ 平倉
- **績效指標**：總報酬、勝率、最大回撤、交易次數

## 免責聲明

本專案僅供教學與研究用途，所有內容不構成任何投資建議。實際交易請自行評估風險。
