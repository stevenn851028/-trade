# 維運手冊

> 本文件涵蓋日常維運工作：**資料存檔排程**、**故障排查**、**監控告警**。
> 隨著系統進入 Phase 1+ 會逐步擴充。

## TAIFEX 日常存檔（Phase 0.5 必做）

### 為什麼一定要立刻設

TAIFEX 公開下載只保留 **最近 30 個交易日**。每過一天，**就有一天的歷史資料從官方消失**。等你正式進 Phase 1 想跑 5 年回測時，這些消失的資料就要付費跟 TAIFEX 申請或用第三方補。

愈早啟動日常存檔，本地累積的乾淨資料就愈多。

### 排程目標

- **頻率**：每日一次
- **時點**：Asia/Taipei **07:00**（夜盤 05:00 收盤後 + 2 小時 buffer，確保 Daily ZIP 已發佈）
- **回溯**：30 天（即使遺漏幾次，下次仍可補上）
- **錯誤容忍**：404 = 休市，正常；network 錯誤下次自動補

### 入口指令

兩種等價方式：

```bash
# 1. 直接呼叫 Python
PYTHONPATH=src python -m twquant.data.cli archive --out-dir data/raw

# 2. 用本 repo 提供的 wrapper（自動 cd、啟 venv、寫 log）
./scripts/archive_daily.sh
```

兩者都會：
- 在 `data/raw/` 補齊缺漏的 `Daily_YYYY_MM_DD.zip`
- 已存在的檔案 **跳過不覆寫**（idempotent）
- 寫月份 log 到 `logs/archive_YYYYMM.log`（僅 wrapper）

### 設定方式

#### macOS / Linux — cron（最簡單）

```bash
crontab -e
```

加入：
```cron
# TAIFEX 每日存檔：07:00 Asia/Taipei
0 7 * * * /絕對路徑/-trade/scripts/archive_daily.sh
```

> ⚠️ 若 VPS 時區非 Taipei，請改成對應的本地時間（如 UTC 則為 `0 23 * * *`，因為 UTC 23:00 = Taipei 07:00）。
> 或在 crontab 上方加 `CRON_TZ=Asia/Taipei` 強制以台北時間解讀。

#### Linux VPS — systemd timer（推薦長期方案）

更可靠，支援錯過時間自動補跑（`Persistent=true`）。

`/etc/systemd/system/twquant-archive.service`：
```ini
[Unit]
Description=TWQuant TAIFEX daily archive
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=youruser
WorkingDirectory=/絕對路徑/-trade
ExecStart=/絕對路徑/-trade/scripts/archive_daily.sh
StandardOutput=append:/絕對路徑/-trade/logs/archive_systemd.log
StandardError=append:/絕對路徑/-trade/logs/archive_systemd.log
```

`/etc/systemd/system/twquant-archive.timer`：
```ini
[Unit]
Description=Run TWQuant archive daily at 07:00 Taipei

[Timer]
OnCalendar=*-*-* 07:00:00 Asia/Taipei
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

啟用：
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now twquant-archive.timer
systemctl list-timers twquant-archive.timer    # 確認下次執行時間
```

手動觸發一次（驗證）：
```bash
sudo systemctl start twquant-archive.service
journalctl -u twquant-archive.service -f
```

#### macOS — launchd（原生方案）

`~/Library/LaunchAgents/com.twquant.archive.plist`：
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
        "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.twquant.archive</string>
    <key>ProgramArguments</key>
    <array>
        <string>/絕對路徑/-trade/scripts/archive_daily.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>7</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/絕對路徑/-trade/logs/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>/絕對路徑/-trade/logs/launchd.log</string>
</dict>
</plist>
```

載入：
```bash
launchctl load ~/Library/LaunchAgents/com.twquant.archive.plist
launchctl list | grep twquant
```

#### Windows — Task Scheduler

最簡單方式：呼叫專案內附的 `scripts/archive_daily.ps1`。

**Step 1：第一次先手動跑一次驗證**

在 PowerShell（venv 啟用後）跑：
```powershell
.\scripts\archive_daily.ps1
Get-Content logs\archive_*.log -Tail 5
```

最後一行應該看到 `exit=0`。

**Step 2：用 PowerShell 建立排程任務（一鍵建立，比 GUI 快）**

在 PowerShell 把下面這段整個複製貼上（**`<>` 內換成你的實際路徑**）：

```powershell
$projectDir = "C:\Users\steven\Documents\GitHub\-trade"   # ← 改成你的
$psExe = (Get-Command powershell).Source
$psArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$projectDir\scripts\archive_daily.ps1`""
$action = New-ScheduledTaskAction -Execute $psExe -Argument $psArgs -WorkingDirectory $projectDir
$trigger = New-ScheduledTaskTrigger -Daily -At 7:00am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "TWQuant Archive" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Download TAIFEX Daily ZIP daily at 07:00"
```

說明：
- 每日 07:00 觸發
- 錯過時間（電腦關機）下次開機自動補跑（`-StartWhenAvailable`）
- 網路暫時不通可重試 3 次（`-RestartCount 3`）

**Step 3：驗證**

```powershell
Get-ScheduledTask -TaskName "TWQuant Archive"
```

或手動觸發測試：
```powershell
Start-ScheduledTask -TaskName "TWQuant Archive"
Start-Sleep -Seconds 30
Get-Content logs\archive_*.log -Tail 10
```

**移除任務**
```powershell
Unregister-ScheduledTask -TaskName "TWQuant Archive" -Confirm:$false
```

#### Windows — Task Scheduler GUI 方式（不想用 PowerShell 建立的話）

1. `Win` + `R` → 輸入 `taskschd.msc` → Enter
2. 右側面板 **建立基本工作**
3. 名稱：`TWQuant Archive`
4. 觸發程序：**每日** → 開始時間 `07:00:00`
5. 動作：**啟動程式**
   - 程式或指令碼：`powershell`
   - 新增引數：
     ```
     -NoProfile -ExecutionPolicy Bypass -File "C:\Users\steven\Documents\GitHub\-trade\scripts\archive_daily.ps1"
     ```
   - 開始位置：`C:\Users\steven\Documents\GitHub\-trade`
6. 勾選「**開啟內容對話方塊**」→ 完成
7. 在跳出的內容對話方塊：
   - **設定** 分頁 → 勾選「**在排定時間開始時若工作未執行**」（錯過自動補跑）
   - 確定

### 驗證已正常運作

1. **手動跑一次**，確認本機網路與下載權限：
   ```bash
   ./scripts/archive_daily.sh
   cat logs/archive_$(date +%Y%m).log
   ```

   預期輸出（最後一行）類似：
   ```
   [archive 2026-03-23..2026-04-22] ok=10, cached=12, holiday(404)=8, errors=0
   ```

2. **檢查檔案**：
   ```bash
   ls -lh data/raw/ | head
   ```
   應該看到一堆 `Daily_YYYY_MM_DD.zip`（每個約 5–30 MB）。

3. **跑 verify 確認可解析**：
   ```bash
   PYTHONPATH=src python -m twquant.data.cli verify \
       --date $(date -v-1d +%Y-%m-%d) \
       --raw-dir data/raw
   ```

### 日常檢查清單（建議每週看一次 log）

- [ ] `errors` 數一直是 0？
- [ ] `ok + cached` 約等於回溯天數（30）扣掉週末（~10 天）扣掉國定假日
- [ ] `data/raw/` 大小持續累積（未來會 > 1 GB / 年）
- [ ] 沒有破損的 0-byte 檔（`find data/raw -size 0`）

### 已知限制

- **只能補 30 天內**：再早的歷史必須另外想辦法（向 TAIFEX 申請、或用 Shioaji / FinMind 補後再對帳）
- **發佈時間有時延遲**：偶見 TAIFEX 06:00 後才上傳新一日檔案；07:00 排程偶爾會撈不到當日檔，下次自動補
- **編碼**：目前預設 Big5；若 TAIFEX 改為 UTF-8 需手動調整 `verify --encoding utf-8`

## 故障排查

### `archive` 跑完 errors > 0

查 `logs/archive_YYYYMM.log` 看具體錯誤：

| 錯誤型態 | 處理 |
| --- | --- |
| `HTTPError 403` | 可能 IP 被擋；嘗試換時段或檢查 User-Agent |
| `HTTPError 5xx` | TAIFEX 暫時性故障；下次重試即可 |
| `URLError` | 本機 / VPS 網路斷線；檢查 connectivity |
| `OSError: No space left` | 磁碟滿了；清舊資料或加掛新硬碟 |

### `verify` 報 PARSE FAIL

嘗試指定編碼：
```bash
PYTHONPATH=src python -m twquant.data.cli verify --date 2026-04-15 \
    --raw-dir data/raw --encoding utf-8
```
若仍失敗，把 ZIP 解開、用 `file -bi xxx.csv` 看實際編碼，回報給我修 parser。

### bars 數不對（< 76 / < 38）

通常代表休市半日、臨時休市、或夜盤縮短。先比對 TAIFEX 行事曆；若日期正常但仍少根，需檢查：
1. ZIP 是否完整（檔案大小、`unzip -t`）
2. tick 時間欄位是否包含夜盤跨日資料
3. 是否選錯主力月（`pick_dominant_month` 邏輯）

## 後續擴充（Phase 1+ 加入）

- [ ] 加入 Telegram / Line 告警：errors > 0 時主動通知
- [ ] 監控 `data/raw/` 連續 N 天未新增 → 告警
- [ ] 加入 Phase 2 paper trading 服務（systemd unit）
- [ ] 加入 Phase 3 實盤交易服務 + healthcheck
- [ ] 部署到 VPS（Docker compose）

## 相關文件

- 資料管線設計：[`DATA_PIPELINE.md`](DATA_PIPELINE.md)
- Phase 0.5 驗證記錄：[`DATA_PIPELINE_FINDINGS.md`](DATA_PIPELINE_FINDINGS.md)
- 路線圖：[`ROADMAP.md`](ROADMAP.md)
