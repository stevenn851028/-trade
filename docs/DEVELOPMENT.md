# 開發規範

## 核心原則

1. **先寫測試，再寫實作**：回測結果的可信度 = 程式碼的正確性
2. **Commit 小、Commit 多**：每個 commit 做一件事，訊息清楚
3. **不合併未過 CI 的程式碼**：tests + lint + type check 全綠才能進主幹
4. **文件與程式碼同步**：改邏輯連帶改 docs

## 環境

| 項目 | 版本／工具 |
| --- | --- |
| Python | 3.11+ |
| 套件管理 | `uv`（優先）或 `pip + venv` |
| 格式化 | `black` + `isort` |
| Lint | `ruff` |
| 型別檢查 | `mypy --strict`（新增程式碼） |
| 測試 | `pytest` + `pytest-cov` |
| 文件 | Markdown（本 repo） |

### 初始化
```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt -r requirements-dev.txt
pre-commit install
```

## 專案結構

見 [`ARCHITECTURE.md`](ARCHITECTURE.md) § 專案目錄結構。

## 分支策略（Gitflow Lite）

```
main                      # 永遠可部署，每次合併都需過 CI + 人工檢視
 └─ develop               # 整合分支（Phase 2+ 啟用）
     ├─ feat/xxx          # 新功能
     ├─ fix/xxx           # 修 bug
     ├─ refactor/xxx      # 重構
     ├─ docs/xxx          # 文件
     └─ exp/xxx           # 實驗分支（可能不合併）
```

Phase 0–1 單人開發，可直接 `feat/* → main`，但仍走 PR 流程以保存決策紀錄。

## Commit 訊息

採 [Conventional Commits](https://www.conventionalcommits.org/) 精簡版：

```
<type>: <subject>

<body>（可選，說明為何這樣做）
```

**type**：`feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `perf`, `data`

**範例**：
```
feat(strategy): 加入 EMA 暖機期檢查

在 EMA 尚未累積 60 根 K 棒前不產生訊號，避免初期誤判。
```

## 程式碼風格

### Python
- 遵循 `black` 預設（88 字元）
- Import 順序：標準庫 → 第三方 → 本專案，由 `isort` 處理
- 所有公開函式需有 type hint
- Docstring 採 Google 風格

### 命名
- 模組、函式：`snake_case`
- 類別：`PascalCase`
- 常數：`UPPER_SNAKE_CASE`
- 內部私有：`_leading_underscore`

### 金融領域慣例
- 時間變數：統一用 `ts`（epoch seconds, UTC）或 `dt`（timezone-aware datetime）
- 價格：`price`、`open/high/low/close`
- 點數 vs 金額：變數名區分清楚（`points` vs `amount_twd`）
- 方向：`LONG` / `SHORT` / `FLAT` 列舉，不用 1/-1/0 魔數

## 測試要求

### 測試分層
- **Unit**：單一類別 / 函式，無外部依賴
- **Integration**：跨模組（如 Strategy + Risk + Executor 的 event 流）
- **End-to-end**：完整回測範例，以固定 seed 驗證結果

### 覆蓋率目標
| 模組 | 目標 |
| --- | --- |
| `strategies/` | ≥ 90% |
| `risk/` | ≥ 90% |
| `execution/` | ≥ 80% |
| `data/` | ≥ 70% |
| 整體 | ≥ 70% |

### 必須測試的情境
- EMA 初始化、跨日、跨週
- 黃金交叉、死亡交叉、無交叉時不應觸發
- 風控攔截（口數超限、保證金不足、Kill Switch）
- 資料缺漏、重複、時區錯誤
- 回測結果可重現（same input → same output）

## CI（GitHub Actions，Phase 1 建立）

Pull Request 觸發：
1. `ruff check` + `black --check`
2. `mypy src/`
3. `pytest --cov --cov-report=xml`
4. 覆蓋率門檻檢查
5. 回測冒煙測試（小範圍資料）

全部通過才能合併。

## 設定管理

所有設定集中在 `configs/*.yaml`，程式碼不寫硬編碼參數：

```yaml
# configs/strategy.yaml
ema_cross_15m:
  timeframe: 15m
  fast_period: 10
  slow_period: 60
  session_mode: combined
  warmup_bars: 60

ema_cross_30m:
  timeframe: 30m
  fast_period: 10
  slow_period: 60
  session_mode: combined
  warmup_bars: 60
```

Phase 1 同步跑 15m 與 30m 兩組，比較後擇優（見 [`STRATEGY.md`](STRATEGY.md)）。

敏感資訊（API key、帳密）走環境變數或 `.env`，**永不** 進 git。

## 秘密管理

- `.env.example`：列出所有需要的環境變數名稱（無值）
- `.env`：本機實際值，已在 `.gitignore`
- 部署時由 systemd EnvironmentFile 或 Docker secret 注入
- Git 歷史發現秘密 → 立即輪替該秘密，然後考慮 `git filter-repo`

## 日誌

- 使用 `loguru`，統一格式（JSON）
- **Level**：
  - `DEBUG`：開發時查錯用，production 關閉
  - `INFO`：每筆訂單、成交、訊號
  - `WARNING`：被風控攔截、資料品質異常
  - `ERROR`：API 錯誤、未預期例外
  - `CRITICAL`：Kill Switch 觸發、程式崩潰

- 所有 `ERROR` 以上同步發 Telegram

## 發版與部署

### 版本號
語意化版本 `MAJOR.MINOR.PATCH`：
- MAJOR：破壞性改變（策略邏輯變動、資料庫 schema 不相容）
- MINOR：新功能（不改變既有行為）
- PATCH：修 bug、效能

### Release 流程（Phase 3+）
1. `main` tag 版本 `v1.2.3`
2. CHANGELOG.md 更新
3. 部署腳本拉該 tag 到 VPS
4. 健康檢查（連接券商、取得即時報價）
5. 確認後開啟實盤交易

### 回滾
- 保留前 3 個版本在 VPS
- 出問題立即切回上一版（< 5 分鐘）

## 研究流程

`notebooks/` 用於資料探索、視覺化、策略發想：
- 命名：`YYYYMMDD_topic.ipynb`
- 檔內需標註結論（即使是「此法無效」）
- 有價值的發現 → 改寫為 `src/` 的正式程式碼

## 討論與決策紀錄

重要決策寫成 ADR（Architecture Decision Record）放在 `docs/adr/`：

```
docs/adr/0001-use-self-built-backtest-engine.md
docs/adr/0002-choose-shioaji-as-primary-broker.md
```

格式：
```markdown
# ADR-0001：自研事件驅動回測引擎

## 背景
...

## 選項
1. backtrader
2. vectorbt
3. 自研

## 決定
選 3。

## 理由
...

## 結果
...
```

## 不做的事

- ❌ 在 `main` 直接 commit（除了 hotfix 緊急狀況）
- ❌ 把 credentials 寫進程式或設定檔
- ❌ 為了讓測試過而降低 coverage 門檻
- ❌ 拷貝貼上 > 3 行程式碼不 refactor
- ❌ 用中文變數名（文件用中文可、程式碼變數一律英文）

## 相關文件

- 系統模組：[`ARCHITECTURE.md`](ARCHITECTURE.md)
- 當前進度：[`ROADMAP.md`](ROADMAP.md)
