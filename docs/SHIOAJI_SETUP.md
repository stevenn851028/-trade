# Shioaji 設定指南

> 一次性設定。完成後即可拉永豐證券 2 年的 1 分 K 歷史資料，並為 Phase 2 paper trading + Phase 3 實盤鋪路。

## Step 1：開永豐金證券戶（3–5 工作日）

**線上申辦**：https://eopen.sinopac.com/

需要：
- 身分證 + 第二證件（健保卡 / 駕照）
- 銀行帳戶（薪轉戶可）
- 手機（接 SMS）
- 年滿 20 歲

**開戶時務必同步申請**：
- ✅ **期貨帳戶**（不是只開證券戶；不開期貨戶就交易不了台指期）
- ✅ **電子下單**
- ✅ **API 程式交易**（沒勾就拿不到 Shioaji key）

完成後等 email 通知（通常 3–5 工作日）。

## Step 2：申請 Shioaji API key

開戶完成後登入 **永豐 e Leader**：https://www.sinotrade.com.tw/

路徑：客戶服務 → API → **申請 API key**

會給你兩串字串：
- `API_KEY`：個人專屬識別
- `SECRET_KEY`：對應密鑰

> ⚠️ **這兩串等同密碼，不要外洩、不要 commit 進 git、不要貼到 Slack**。

## Step 3：安裝 Shioaji 套件

在你 venv 啟用的 PowerShell：

```powershell
pip install shioaji
```

預期看到 `Successfully installed shioaji-x.y.z`。

## Step 4：設定環境變數（每次新開終端機都要）

PowerShell（**只對當前視窗有效**）：

```powershell
$env:SHIOAJI_API_KEY = "你的 api key"
$env:SHIOAJI_SECRET_KEY = "你的 secret key"
```

**永久設定**（推薦，省去每次手動設）：

```powershell
[Environment]::SetEnvironmentVariable("SHIOAJI_API_KEY", "你的 api key", "User")
[Environment]::SetEnvironmentVariable("SHIOAJI_SECRET_KEY", "你的 secret key", "User")
```

設完**關掉重開** PowerShell 才會生效。

## Step 5：第一次連線測試（拉一天資料試試）

```powershell
python -m twquant.data.cli shioaji-import `
    --start 2025-05-01 --end 2025-05-02 `
    --db data\db\bars.sqlite `
    --symbol TXFR1
```

預期看到類似：
```
Shioaji TXFR1 1m 2025-05-01..2025-05-02
  raw 1m bars: 1,200+
  upserted 1m: 1,200+
  upserted 15m: 76
  upserted 30m: 38
```

> 第一次連線可能跳出 Shioaji 憑證對話框，照指示安裝即可。

## Step 6：拉滿 2 年歷史

確認試跑 OK 後，拉滿你要的區間：

```powershell
python -m twquant.data.cli shioaji-import `
    --start 2023-06-01 --end 2025-06-01 `
    --db data\db\bars.sqlite `
    --symbol TXFR1
```

2 年資料約 30 分鐘完成（API 一次最多 25 日，本 CLI 自動 chunk）。完成後：

```powershell
python -m twquant.data.cli stats --db data\db\bars.sqlite
```

預期看到：
```
TX 1m:    ~270,000 bars  [2023-06-01 → 2025-06-01]
TX 15m:    ~18,000 bars  [...]
TX 30m:     ~9,000 bars  [...]
TX 1d:    ~33,000 bars   (from earlier FinMind import)
```

## Step 7：跑真實 15m / 30m 回測 + walk-forward

```powershell
python -m twquant.backtest.cli run --strategy ema_cross_15m `
    --db data\db\bars.sqlite --start 2023-06-01 --end 2025-06-01 `
    --output results\real_15m_2y

python -m twquant.backtest.cli run --strategy ema_cross_30m `
    --db data\db\bars.sqlite --start 2023-06-01 --end 2025-06-01 `
    --output results\real_30m_2y

python -m twquant.backtest.cli compare results\real_15m_2y results\real_30m_2y
```

Walk-forward（2 年資料可跑 train=12m / test=3m）：

```powershell
python -m twquant.backtest.cli walk-forward --strategy ema_cross_30m `
    --db data\db\bars.sqlite --start 2023-06-01 --end 2025-06-01 `
    --train-months 12 --test-months 3 `
    --output results\wf_real_30m
```

## 常見問題

### Q1：登入時跳「目前處於非營業時段」
Shioaji 假日 / 收盤後仍可登入查歷史，但即時報價可能有限制。歷史資料拉取不影響。

### Q2：API rate limit
Shioaji 一次 kbars 請求最多回 30 日資料。本 CLI 自動分 25 日 chunk，不易被擋。若一次拉很長區間（例如 5 年）建議：
- 一次拉 6 個月：`--start 2023-01-01 --end 2023-06-30`
- 跑完再下一段

### Q3：忘記 secret key
e Leader 後台可重新產生（會作廢舊的）。

### Q4：要不要付月費版？
不需要。免費 API 額度對歷史回填夠用。Phase 2 之後若需要即時 tick 串流再評估。

### Q5：環境變數設了但 CLI 仍說缺
- PowerShell 視窗沒重開（永久設定後要關掉重開才生效）
- 或用 `--api-key xxx --secret-key yyy` 直接傳

## 相關文件

- 系統架構：[`ARCHITECTURE.md`](ARCHITECTURE.md)
- 資料管線：[`DATA_PIPELINE.md`](DATA_PIPELINE.md)
- 策略規格：[`STRATEGY.md`](STRATEGY.md)
- 路線圖：[`ROADMAP.md`](ROADMAP.md)
