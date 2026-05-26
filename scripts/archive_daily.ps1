# TAIFEX 日常存檔 wrapper (Windows PowerShell)
#
# 用途：給 Windows Task Scheduler 呼叫的單一進入點。
# 對應 scripts/archive_daily.sh 的 PowerShell 版本。
#
# 排程設定詳見 docs/OPERATIONS.md。

$ErrorActionPreference = 'Continue'

# 專案根目錄：本腳本所在的上一層
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

# 啟用 venv（若存在）
$VenvActivate = Join-Path $ProjectDir ".venv\Scripts\Activate.ps1"
if (Test-Path $VenvActivate) {
    & $VenvActivate
}

# Log 目錄與當月檔案
$LogDir = Join-Path $ProjectDir "logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}
$LogFile = Join-Path $LogDir ("archive_" + (Get-Date -Format "yyyyMM") + ".log")

# 環境變數（可由 Task Scheduler 覆寫）
$Lookback = if ($env:ARCHIVE_LOOKBACK) { $env:ARCHIVE_LOOKBACK } else { 30 }
$Timeout = if ($env:ARCHIVE_TIMEOUT) { $env:ARCHIVE_TIMEOUT } else { 30 }

# 執行並把 stdout + stderr 寫進 log（append）
$Stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
Add-Content -Path $LogFile -Value "===== $Stamp archive run =====" -Encoding utf8

$OutDir = Join-Path $ProjectDir "data\raw"
$cmdArgs = @(
    "-m", "twquant.data.cli", "archive",
    "--out-dir", $OutDir,
    "--lookback", $Lookback,
    "--timeout", $Timeout
)

# 注意：Python logging 預設寫 stderr，PowerShell 會把 stderr 包成 ErrorRecord 物件。
# 用 `Out-String -Stream` 把所有物件（含 ErrorRecord）轉成單純字串，
# 避免 log 裡塞一堆 NativeCommandError / RemoteException 雜訊。
& python @cmdArgs 2>&1 | Out-String -Stream | Add-Content -Path $LogFile -Encoding utf8
$ExitCode = $LASTEXITCODE

Add-Content -Path $LogFile -Value "===== exit=$ExitCode =====" -Encoding utf8
Add-Content -Path $LogFile -Value "" -Encoding utf8

exit $ExitCode
