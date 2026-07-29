# mh-archive-pipeline 專案完整技術說明

> 分析基準：`main` 分支，Commit `7d5c2dd609c9d30af511c8bc176b715461a22a3a`  
> Repo：`bruce851117/mh-archive-pipeline`  
> 匯出內容：163 個 Git 追蹤文字檔，未略過任何檔案  
> 文件用途：說明所有主要程式、GitHub Actions、設定檔、資料目錄、輸入／輸出路徑、更新方式及維護風險。

---

## 1. 專案用途與核心結論

這個 Repo 是一套以 **FinancialJuice RSS** 為主要新聞來源的金融市場新聞管線。它將英文快訊按台北時間歸檔，再交給 **Google Gemini API** 做分類、翻譯、去重、重要性評分與摘要，最後生成供靜態網頁使用的 JSON。

主資料流如下：

```text
FinancialJuice RSS
    ↓ fetch_rss.py
每日原始新聞 archive + 最近24小時 latest_24h
    ↓ daily_digest.py
市場每日摘要 + Debug + 五大央行官員談話摘要
    ↓ build_web_news.py
網頁顯示資料 data/web/latest.json
    ↓ index.html
市場新聞 Dashboard
```

另有一條獨立的央行歷史補抓支線：

```text
data/archive + manual_central_bank_backfill.json
    ↓ backfill_central_bank_headlines.py
央行 raw 日檔 + latest_90d.json
    ↓ daily_digest.py
央行官員近90日談話摘要
```

### 最重要的資料更新特性

- `data/archive/...`：按 `id` 合併，只新增未出現過的新聞；不是整份以新抓結果取代舊歷史。
- `data/latest_24h.json`：每次抓取後重新計算最近24小時，因此會整份重建。
- `data/digests/...`、`data/debug/...`：同一天再次執行時會改寫當日結果。
- `data/latest_daily_digest.json`、`data/latest_digest_debug.json`：永遠代表最近一次摘要執行，會整份覆蓋。
- `data/web/latest.json`：前端唯一主要資料來源，每次 `build_web_news.py` 執行都整份覆蓋。
- `data/central_banks/raw/...`：按 `id` 增量合併。
- `data/central_banks/latest_90d.json`：每次補抓後，從央行 raw 日檔重新彙整近90天，整份覆蓋。
- 所有主要 Python 寫檔均採 `.tmp` 後 `replace` 的原子寫入方式，降低半寫入造成 JSON 損壞的風險。

---

## 2. Repo 結構總覽

```text
.
├── .github/workflows/
│   ├── backfill-central-banks.yml
│   ├── daily-digest.yml
│   ├── export_repo_context.yml
│   ├── fetch-rss.yml
│   └── test_wsj.yml
├── PIPELINE.md
├── fetch_rss.py
├── daily_digest.py
├── build_web_news.py
├── backfill_central_bank_headlines.py
├── test_wallstreetcn_breakfast.py
├── central_bank_officials.json
├── manual_central_bank_backfill.json
├── requirements.txt
├── index.html
└── data/
    ├── archive/
    ├── debug/
    ├── digests/
    ├── central_banks/
    │   ├── raw/
    │   └── digests/
    └── web/
```

---

## 3. GitHub Actions 工作流程

## 3.1 `.github/workflows/fetch-rss.yml`

### 用途

執行 FinancialJuice RSS 抓取與歸檔。

### 觸發方式

Workflow 本身只宣告：

```yaml
on:
  workflow_dispatch:
```

因此 GitHub Actions 內沒有 `schedule`。若系統確實每小時自動執行，排程來自 Repo 外部的 **Cloudflare Worker**，由 Worker 呼叫 GitHub `workflow_dispatch` API。

### 執行順序

1. Checkout Repo，`fetch-depth: 0`。
2. 建立 Python 3.12。
3. 安裝 `requirements.txt`。
4. 執行 `python fetch_rss.py`。
5. 顯示 `data/` 產出清單及 `data/fetch_status.json`。
6. `git add data`，有異動才 Commit、Push。

### 主要輸入

- FinancialJuice RSS：`https://www.financialjuice.com/feed.ashx?xy=rss`
- 既有 `data/archive/...`，供合併與去重。

### 主要輸出

- `data/archive/YYYY/MM/YYYY-MM-DD.json`
- `data/latest_24h.json`
- `data/fetch_status.json`

### 注意事項

這個 Workflow 在 Push 前沒有先 `git pull --rebase`。雖有 concurrency group 防止同一 Workflow 互撞，但仍可能與 `daily-digest.yml` 或其他會 Push 的 Workflow 發生競態。

---

## 3.2 `.github/workflows/daily-digest.yml`

### 用途

一次完成三件事：

1. 產生市場每日 Gemini 摘要。
2. 產生五大央行官員近90日談話摘要。
3. 產生前端使用的 `data/web/latest.json`。

### 環境變數

```text
GEMINI_API_KEY = GitHub Secret
GEMINI_MODEL   = gemini-3.6-flash
TZ             = Asia/Taipei
```

### 執行順序

1. Checkout Repo。
2. Python 3.12 + pip cache。
3. 安裝依賴。
4. 編譯檢查 `daily_digest.py` 與 `build_web_news.py`。
5. 驗證三項輸入：
   - `data/latest_24h.json`
   - `central_bank_officials.json`
   - `data/central_banks/latest_90d.json`
6. 執行 `daily_digest.py`。
7. 驗證市場摘要、Debug、狀態檔。
8. 驗證央行 digest 與狀態檔。
9. 執行 `build_web_news.py`。
10. 驗證網頁 JSON 與狀態檔。
11. 在 Log 顯示 Gemini 留下與刪除的 headline。
12. Commit `data/digests`、`data/debug`、`data/web`、`data/central_banks` 及最新／狀態檔。
13. `git pull --rebase origin main` 後 Push。

### 觸發方式

同樣只有 `workflow_dispatch`。`PIPELINE.md` 記載每天台灣時間 07:00 由 Cloudflare Worker 觸發；排程不在此 Repo 的 YAML 內。

---

## 3.3 `.github/workflows/backfill-central-banks.yml`

### 用途

手動掃描最近90天一般新聞 archive，重新辨識並補齊 Fed、BoE、ECB、BoJ、RBA 官員相關 headline。

### 輸入

- `backfill_central_bank_headlines.py`
- `central_bank_officials.json`
- `manual_central_bank_backfill.json`
- `data/archive/`

### 輸出

- `data/central_banks/raw/YYYY/MM/YYYY-MM-DD.json`
- `data/central_banks/latest_90d.json`
- `data/central_banks/backfill_status.json`

### 更新方式

- raw 日檔：按 `id` 增量合併，只加入新項目。
- `latest_90d.json`：掃描 raw 目錄後整份重建。
- Workflow 最後 `git pull --rebase origin main` 再 Push。

---

## 3.4 `.github/workflows/test_wsj.yml`

### 用途

測試「華爾街見聞（WallstreetCN）」早餐文章搜尋、文章抓取與「要聞」區塊解析。

### 手動輸入

- `target_date`：指定日期，留空使用台北當天。
- `article_id`：可直接指定文章 ID。

### 產出

產出放在 Runner 暫存目錄 `wscn_breakfast_test_output/`，並上傳為 GitHub Actions Artifact，保留7天，不 Commit 回 Repo。

主要測試產物包括：

- `00_report.json`
- 搜尋 API 原始回應與正規化紀錄
- 文章 API 回應
- HTML 內容與解析結果
- `wscn_breakfast_test.log`

即使測試失敗，Artifact 仍會上傳，最後再將 Workflow 標記為失敗。

---

## 3.5 `.github/workflows/export_repo_context.yml`

### 用途

將所有 Git 已追蹤的文字檔合併為：

```text
REPO_FULL_DUMP.txt
```

這是為了專案交接／AI 程式碼分析，不是正式新聞資料流程的一部分。

### 行為

- 使用 `git ls-files` 取得追蹤檔。
- 排除 `.env`、credentials、service account、secrets 等常見敏感檔。
- 支援 Python、JS、HTML、JSON、CSV、Markdown、YAML 等文字檔。
- 排除二進位檔與單檔超過20MB的檔案。
- 強制 `git add -f REPO_FULL_DUMP.txt` 並 Push 至 `main`。

### 維護建議

`REPO_FULL_DUMP.txt` 會複製全 Repo 大量內容並持續 Commit，容易放大 Repo 體積，而且公開 Repo 中可能暴露程式內硬編碼資訊。分析完成後可考慮刪除 Workflow 與 Dump，或將 Dump 改成 Artifact 而非 Commit。

---

## 4. Python 程式說明

## 4.1 `fetch_rss.py`

### 功能

抓取 FinancialJuice RSS，清理 headline、轉換時間、建立唯一 ID、寫入台北日期 archive，並產生最近24小時資料。

### 核心處理

- 下載 RSS XML。
- 清理 HTML entity、多餘空白與 `FinancialJuice:` 前綴。
- 將發布時間轉為 `Asia/Taipei`、`+08:00`。
- 以 GUID、Link 或 headline + time 產生穩定 ID。
- 同一天資料以 `id` 合併去重。
- 重建最近24小時清單。
- 成功或失敗均寫狀態檔。

### 輸入／輸出

```text
外部輸入：FinancialJuice RSS
既有輸入：data/archive/YYYY/MM/*.json

輸出：
data/archive/YYYY/MM/YYYY-MM-DD.json
data/latest_24h.json
data/fetch_status.json
```

### 覆蓋規則

archive 並非「把整天舊資料全部換成目前 RSS 內容」。它會先讀既有日檔，再以 `id` 合併新資料；舊資料保留，新資料追加。日檔最後雖然以整份 JSON 寫回，但邏輯是增量合併。

---

## 4.2 `daily_digest.py`

這是 Repo 中功能最重的程式，包含三大模組：市場摘要、WallstreetCN 早餐補充、央行官員談話整理。

### A. 市場每日摘要

#### 輸入

- `data/latest_24h.json`
- `GEMINI_API_KEY`
- `GEMINI_MODEL`

#### 前處理

- 清理必要欄位。
- 先按 `id` 去重。
- 將 headline 轉小寫並去除非英數字元，再去掉文字完全重複項；保留最早一則。
- 送給 Gemini 的欄位只包含 `id`、`time`、`headline`。
- 最大輸入限制為 500,000 字元。

#### Gemini 任務

- 六大類：債市、股市、央行、財政政策、經濟、戰爭。
- 翻譯為繁體中文。
- 對保留事件評 1–5 分。
- 依規則刪除市場意義低的 headline。
- 僅能使用輸入已有的 `source_id`，降低虛構事件風險。
- HTTP 429、500、502、503、504 最多重試3次。
- Timeout 300秒，`temperature=0.1`。

#### 輸出

```text
data/digests/YYYY/MM/YYYY-MM-DD.json
data/latest_daily_digest.json
data/debug/YYYY/MM/YYYY-MM-DD.json
data/latest_digest_debug.json
data/digest_status.json
data/digests/history_index.json
```

### B. WallstreetCN 早餐

主程式會嘗試：

1. 搜尋當天標題含「早餐」與中文年月日的文章。
2. 抓取文章 API。
3. 從 HTML 中尋找「要聞／要闻」H2 後的第一個 `blockquote`。
4. 將其中粗體短行視為分類標題，其後段落／清單視為事件。

這段採「best effort」：搜尋或解析失敗只印 Warning，不應阻斷原本 FinancialJuice Digest。

外部端點：

```text
https://api-one-wscn.awtmt.com/apiv1/search/article
https://api-one.wallstcn.com/apiv1/search/article
https://api-one-wscn.awtmt.com/apiv1/content/articles/{article_id}?extract=0
https://api-one.wallstcn.com/apiv1/content/articles/{article_id}?extract=0
```

### C. 五大央行官員摘要

#### 輸入

- `data/central_banks/latest_90d.json`
- `central_bank_officials.json`

#### 處理規則

- 涵蓋 Fed、BoE、ECB、BoJ、RBA。
- 依央行、官員、日期分組。
- 同一官員同一天多則 headline 合併為一句。
- 只保留經濟、通膨、勞動市場、利率、貨幣政策、資產負債表、QE／QT。
- 排除金融監管、銀行資本、支付、加密貨幣、行政與行程內容。
- 官員必須能對應設定檔的 `display_name`。
- 每筆摘要100個中文字以內。

#### 輸出

```text
data/central_banks/digests/YYYY/MM/YYYY-MM-DD.json
data/central_banks/digests/latest.json
data/central_banks/digest_status.json
```

---

## 4.3 `build_web_news.py`

### 功能

將每日 digest 整理成 `index.html` 可直接使用的單一 JSON。

### 顯示區間

以最近 RSS 成功抓取時間作為資料截止點，而不是摘要程式執行時間：

- 週二至週五：前一日17:00至資料截止時間。
- 週六、週日、週一：上週五17:00至資料截止時間。

### 處理

1. 尋找區間涉及的 digest 日檔。
2. 篩選 `event_time` 落在區間內的事件。
3. 按 `source_id` 去重；同 ID 保留重要性較高版本。
4. 再呼叫 Gemini，選出5–10點首頁摘要。
5. 依六大類組成 blocks。
6. `importance_score >= 4` 設為 `highlight: true`。
7. 每一分類內按新聞時間由早到晚排序。

### 輸入／輸出

```text
輸入：
data/digests/YYYY/MM/*.json
data/fetch_status.json
GEMINI_API_KEY

輸出：
data/web/latest.json
data/web/status.json
```

### `data/web/latest.json` 主要欄位

```text
generated_at
summary_generated_at
data_updated_at
timezone
period_start
period_end
model
source_digest_files
event_count
summary_points
blocks
usage_metadata
```

---

## 4.4 `backfill_central_bank_headlines.py`

### 功能

掃描最近90天一般新聞 archive，依央行所有格前綴挑選央行 headline，並與人工補充資料合併。

### 辨識方式

使用 `central_bank_officials.json` 的前綴：

```text
FED：fed's / fed’s
BOE：boe's / boe’s
ECB：ecb's / ecb’s
BOJ：boj's / boj’s
RBA：rba's / rba’s
```

它先辨識是哪一家央行，不是在這一步辨識具體官員；具體官員交由後續 Gemini Digest 配合官員名單處理。

### 資料來源

- `data/archive/` 最近90天日檔。
- `manual_central_bank_backfill.json` 人工補充資料。
- `central_bank_officials.json` 的 `filter_prefixes`。

### 輸出

```text
data/central_banks/raw/YYYY/MM/YYYY-MM-DD.json
data/central_banks/latest_90d.json
data/central_banks/backfill_status.json
```

### 合併規則

- 以新聞 `id` 去重。
- 既有 raw 項目優先保留；incoming 只在 ID 尚不存在時加入。
- `latest_90d.json` 從所有 raw 日檔重新掃描、限制90天後整份重建。

---

## 4.5 `test_wallstreetcn_breakfast.py`

### 功能

將 WallstreetCN 早餐抓取拆成可單獨診斷的測試流程，保存每一步原始資料，避免正式流程只顯示「找不到文章」但無法判斷是搜尋、文章 API 還是 HTML 解析問題。

### 使用情境

- 測試 WallstreetCN 搜尋 API 是否改版。
- 比對兩組 API Domain 回應。
- 指定日期或 article ID。
- 查看原始 HTML 與正規化後的搜尋紀錄。

### 產出位置

預設輸出到：

```text
wscn_breakfast_test_output/
```

Workflow 執行時作為 Artifact 上傳，不寫進 `data/`。

---

## 5. 設定與靜態檔

## 5.1 `central_bank_officials.json`

### 用途

- 定義五大央行辨識前綴。
- 定義官員 `headline_name`、正式 `display_name`、職位、優先級及是否 active。
- 供央行談話 Gemini prompt 統一姓名與排序。

### 結構

```text
version
timezone
description
filter_prefixes
central_banks
  └── FED / BOE / ECB / BOJ / RBA
      ├── display_name
      └── officials[]
          ├── priority
          ├── headline_name
          ├── display_name
          ├── position
          └── active
```

### 維護重點

官員異動時需要同步更新；若官員姓名或新聞中的姓氏無法對上，後續 Gemini 可能直接捨棄該筆。

---

## 5.2 `manual_central_bank_backfill.json`

人工維護的央行歷史 headline 補充檔。`backfill_central_bank_headlines.py` 會優先讀取後，再與一般 archive 按 ID 合併。

適合補入：

- Workflow 尚未上線前的歷史資料。
- RSS 遺漏資料。
- 前綴規則未匹配但確認屬央行官員談話的資料。

---

## 5.3 `requirements.txt`

定義 Python 套件依賴，由 `fetch-rss.yml` 與 `daily-digest.yml` 安裝。核心外部 HTTP 呼叫使用 `requests`；RSS 解析相關套件依檔案實際版本為準。

---

## 5.4 `index.html`

純靜態前端，讀取：

```text
./data/web/latest.json
```

依 `summary_points` 與 `blocks` 渲染首頁摘要及六大新聞分類。它不是資料生成者，而是最終資料消費端。

---

## 5.5 `PIPELINE.md`

Repo 原有的流程說明文件，描述 Cloudflare Worker、GitHub Actions、三階段新聞流程、時區與手動觸發方式。

需注意其中模型名稱曾寫為 `gemini-3.5-flash`，但目前 Workflow 與 Python 預設值為 `gemini-3.6-flash`；應以實際 Workflow／程式為準，並修正文檔避免版本不一致。

---

## 6. Data 資料夾完整地圖

## 6.1 一般市場新聞

| 路徑 | 產生程式 | 主要用途 | 更新方式 |
|---|---|---|---|
| `data/archive/YYYY/MM/YYYY-MM-DD.json` | `fetch_rss.py` | 每日原始 RSS 永久歷史 | 按 ID 合併後整檔寫回，只新增未見 ID |
| `data/latest_24h.json` | `fetch_rss.py` | 最新24小時摘要輸入 | 每次整份重建 |
| `data/fetch_status.json` | `fetch_rss.py` | 抓取狀態、時間與筆數 | 每次覆蓋 |
| `data/digests/YYYY/MM/YYYY-MM-DD.json` | `daily_digest.py` | 當日市場摘要 | 同日重跑會覆蓋 |
| `data/latest_daily_digest.json` | `daily_digest.py` | 最新市場摘要 | 每次覆蓋 |
| `data/debug/YYYY/MM/YYYY-MM-DD.json` | `daily_digest.py` | Gemini 保留／刪除項目 | 同日重跑會覆蓋 |
| `data/latest_digest_debug.json` | `daily_digest.py` | 最新一次 Debug | 每次覆蓋 |
| `data/digest_status.json` | `daily_digest.py` | 摘要成功／失敗狀態 | 每次覆蓋 |
| `data/digests/history_index.json` | `daily_digest.py` | Digest 歷史索引 | 重新整理／覆蓋 |

## 6.2 央行資料

| 路徑 | 產生程式 | 主要用途 | 更新方式 |
|---|---|---|---|
| `data/central_banks/raw/YYYY/MM/YYYY-MM-DD.json` | `backfill_central_bank_headlines.py` | 每日央行原始 headline | 按 ID 增量合併 |
| `data/central_banks/latest_90d.json` | 同上 | 最近90天央行摘要輸入 | 從 raw 重新彙整後覆蓋 |
| `data/central_banks/backfill_status.json` | 同上 | Backfill 狀態 | 每次覆蓋 |
| `data/central_banks/digests/YYYY/MM/YYYY-MM-DD.json` | `daily_digest.py` | 央行談話 Digest 歷史 | 按執行日期寫入／同日可覆蓋 |
| `data/central_banks/digests/latest.json` | `daily_digest.py` | 最新90天官員談話整理 | 每次覆蓋 |
| `data/central_banks/digest_status.json` | `daily_digest.py` | 央行 Digest 狀態 | 每次覆蓋 |
| `data/central_banks/filter_status.json` | 央行篩選／歷史流程產物 | 記錄篩選狀態 | 狀態檔 |

## 6.3 前端資料

| 路徑 | 產生程式 | 主要用途 | 更新方式 |
|---|---|---|---|
| `data/web/latest.json` | `build_web_news.py` | `index.html` 唯一主要新聞資料 | 每次整份覆蓋 |
| `data/web/status.json` | `build_web_news.py` | 網頁資料生成狀態 | 每次覆蓋 |

---

## 7. 主要 JSON Schema

## 7.1 Archive 新聞項目

```json
{
  "id": "唯一ID",
  "guid": "RSS GUID",
  "published_at": "ISO 8601 +08:00",
  "timezone": "Asia/Taipei",
  "utc_offset": "+08:00",
  "taipei_date": "YYYY-MM-DD",
  "headline": "清理後Headline",
  "original_title": "RSS原始標題",
  "link": "新聞連結",
  "categories": [],
  "source": "FinancialJuice",
  "rss_url": "RSS網址",
  "fetched_at": "抓取時間"
}
```

## 7.2 Market Digest

主要包含：

```text
title
period_start
period_end
timezone
model
input_count
categories[]
  ├── category
  └── news[]
      ├── source_id
      ├── headline_zh
      ├── summary_zh
      ├── importance_score
      └── event_time
discarded
usage_metadata
```

實際欄位以當前 `daily_digest.py` 的正規化輸出為準。

## 7.3 Web JSON

```json
{
  "generated_at": "...",
  "summary_generated_at": "...",
  "data_updated_at": "...",
  "timezone": "Asia/Taipei",
  "period_start": "...",
  "period_end": "...",
  "model": "gemini-3.6-flash",
  "source_digest_files": [],
  "event_count": 0,
  "summary_points": [],
  "blocks": [
    {
      "category": "債市",
      "news": []
    }
  ],
  "usage_metadata": {}
}
```

## 7.4 央行 raw 項目

```json
{
  "id": "...",
  "published_at": "...",
  "timezone": "Asia/Taipei",
  "utc_offset": "+08:00",
  "taipei_date": "YYYY-MM-DD",
  "central_bank": "FED",
  "headline": "...",
  "source": "FinancialJuice",
  "fetched_at": "..."
}
```

---

## 8. 外部服務與 Secrets

| 外部服務 | 用途 | 認證／設定 |
|---|---|---|
| FinancialJuice RSS | 原始金融快訊 | 未見 API Key |
| Google Gemini API | 分類、翻譯、評分、摘要、央行談話整理 | `GEMINI_API_KEY` GitHub Secret |
| WallstreetCN API | 早餐要聞補充與測試 | 未見 API Key，以 HTTP Header 模擬瀏覽器 |
| GitHub Actions API | 由 Cloudflare Worker觸發 Workflow | Worker 端 `GITHUB_TOKEN` |
| Cloudflare Worker | 外部 Cron 與手動 trigger endpoint | 不在本 Repo 內 |

不得將真實 Token、API Key 或密碼放進 Repo、JSON 或 `REPO_FULL_DUMP.txt`。

---

## 9. 排程與時間基準

### Repo 內可確認

所有主要 Workflow 都是 `workflow_dispatch`，Repo 內沒有 GitHub cron。

### 原文件記載的外部排程

```text
每小時第50分：觸發 fetch-rss.yml
每日台灣07:00：觸發 daily-digest.yml
```

Cloudflare Cron 使用 UTC，因此台灣07:00對應前一日23:00 UTC。

### 時區策略

- 寫入及展示統一使用 `Asia/Taipei`、UTC+8。
- 解析無時區時間時，多數程式預設視為台北時間。
- 最近24小時計算及顯示區間會使用 timezone-aware datetime。

---

## 10. 完整資料流對照

```text
[外部排程 Cloudflare Worker]
       │
       ├─ workflow_dispatch → fetch-rss.yml
       │                         │
       │                         └─ fetch_rss.py
       │                              ├─ 讀 FinancialJuice RSS
       │                              ├─ 讀既有 data/archive
       │                              ├─ 寫 data/archive/YYYY/MM/日期.json
       │                              ├─ 寫 data/latest_24h.json
       │                              └─ 寫 data/fetch_status.json
       │
       ├─ workflow_dispatch → backfill-central-banks.yml（手動）
       │                         │
       │                         └─ backfill_central_bank_headlines.py
       │                              ├─ 讀 data/archive
       │                              ├─ 讀 manual_central_bank_backfill.json
       │                              ├─ 讀 central_bank_officials.json
       │                              ├─ 寫 data/central_banks/raw
       │                              ├─ 寫 data/central_banks/latest_90d.json
       │                              └─ 寫 backfill_status.json
       │
       └─ workflow_dispatch → daily-digest.yml
                                 │
                                 ├─ daily_digest.py
                                 │    ├─ 讀 data/latest_24h.json
                                 │    ├─ 呼叫 Gemini
                                 │    ├─ 寫 data/digests
                                 │    ├─ 寫 data/debug
                                 │    ├─ 讀 data/central_banks/latest_90d.json
                                 │    ├─ 讀 central_bank_officials.json
                                 │    └─ 寫 data/central_banks/digests
                                 │
                                 └─ build_web_news.py
                                      ├─ 讀 data/digests
                                      ├─ 讀 data/fetch_status.json
                                      ├─ 呼叫 Gemini做首頁摘要
                                      └─ 寫 data/web/latest.json
                                               │
                                               └─ index.html載入顯示
```

---

## 11. 已發現的重要維護問題與風險

### 11.1 文件與實際模型版本不一致

`PIPELINE.md` 部分文字寫 `gemini-3.5-flash`，實際 `daily-digest.yml`、`daily_digest.py`、`build_web_news.py` 使用 `gemini-3.6-flash`。應統一，避免未來維護者誤判。

### 11.2 外部排程不在 Repo 內

Repo 只看得到 `workflow_dispatch`，無法單靠 Repo 還原 Cloudflare Worker 程式、Cron、Token 權限與失敗重試。建議把 Worker 程式及部署說明另存於 Repo 的 `cloudflare/` 或文件目錄。

### 11.3 Workflow Push 競態

`daily-digest.yml` 與 backfill Workflow 推送前有 `git pull --rebase`，但 `fetch-rss.yml` 未做 rebase。不同 Workflow 同時更新 `data/` 時，fetch Push 可能被拒絕。建議統一加入 rebase + retry，或讓所有資料更新使用同一 concurrency group。

### 11.4 Repo 被當作資料庫，規模會持續成長

Archive、Digest、Debug、央行 raw、狀態與 Dump 都 Commit 入 Git。即使刪除舊檔，Git 歷史仍保留，Clone 與 Actions Checkout 會逐漸變慢。

### 11.5 Gemini 結果具有非決定性

雖然 temperature 很低、限定 source ID 且有 schema 檢查，同一批資料重跑仍可能得到不同分類、摘要或分數。同日檔會被覆蓋，因此若要追蹤模型結果變化，需額外保存 run ID／prompt version／model version。

### 11.6 央行前綴辨識可能漏資料

Backfill 只靠 `Fed's`、`ECB's` 等所有格前綴，若 headline 是 `Fed Chair ...`、`Lagarde ...`、`Bank of England ...` 或其他格式，可能無法進入央行 raw。人工 backfill 能補救，但仍需定期檢查漏抓。

### 11.7 官員名單需要持續維護

具體官員必須對應 `central_bank_officials.json`。人事異動、拼字、重音符號或新聞使用不同姓氏格式，都可能造成摘要遺漏。

### 11.8 WallstreetCN API 與 HTML 結構風險

該功能依賴未保證穩定的搜尋／文章 API 與「H2 要聞後第一個 blockquote」結構。網站改版時可能靜默跳過，不一定使主流程失敗。

### 11.9 `REPO_FULL_DUMP.txt` 不宜長期保留

它會造成內容重複、Repo 膨脹與潛在資訊曝光。建議分析完成後刪除，未來改成 Actions Artifact。

### 11.10 Status 檔不可取代監控

目前狀態檔記錄最後一次執行結果，但若 Workflow 根本沒有被外部 Scheduler 觸發，狀態檔不會自動顯示「排程漏跑」。建議外部監控檢查 `last_success_at` 是否過期。

---

## 12. 建議的維護優先順序

### P0：正確性與不中斷

1. 為 `fetch-rss.yml` 加入 `git pull --rebase` 與 Push retry。
2. 統一主要 Workflow concurrency，避免跨 Workflow 同時 Push。
3. 建立「最後成功時間超過門檻」告警。
4. 定期驗證 `latest_24h.json` 非空、時間區間正確、最新資料沒有停止更新。

### P1：資料品質

1. 將央行辨識由單純所有格前綴擴充為官員姓名＋央行全名規則。
2. 為 Gemini Output 增加 JSON Schema 驗證與失敗樣本保存。
3. 在 Digest 存入 `prompt_version`、實際模型版本、Workflow run ID。
4. 為 archive、digest、web 建立固定 schema 測試。

### P2：維護性

1. 更新 `PIPELINE.md` 的模型版本。
2. 把 Cloudflare Worker 程式與部署方式納入版本控制。
3. 將共用 JSON／時間／重試函式抽成模組，避免多支程式重複。
4. 將大量歷史資料移至 Release、Object Storage、資料庫或定期壓縮檔。
5. 將 Repo Context Dump 改成 Artifact。

---

## 13. 快速操作指南

### 手動抓 RSS

```text
GitHub → Actions → Fetch FinancialJuice RSS → Run workflow
```

### 手動重跑每日摘要

```text
GitHub → Actions → Generate Daily Gemini Digest → Run workflow
```

執行前確認：

- `data/latest_24h.json` 非空。
- `data/central_banks/latest_90d.json` 存在。
- `GEMINI_API_KEY` 已設定。

### 手動補抓央行資料

```text
GitHub → Actions → Backfill Central Bank Headlines → Run workflow
```

### 測試 WallstreetCN 早餐

```text
GitHub → Actions → Test WallstreetCN Breakfast → Run workflow
```

可填日期或文章 ID；結果從該次 Run 的 Artifact 下載。

---

## 14. 最終檔案責任對照

| 檔案／目錄 | 讀取者 | 寫入者 |
|---|---|---|
| `data/archive/` | Backfill、人工分析 | `fetch_rss.py` |
| `data/latest_24h.json` | `daily_digest.py` | `fetch_rss.py` |
| `data/fetch_status.json` | `build_web_news.py`、Workflow | `fetch_rss.py` |
| `data/digests/` | `build_web_news.py`、歷史查詢 | `daily_digest.py` |
| `data/debug/` | GitHub Actions Log、人工除錯 | `daily_digest.py` |
| `data/latest_daily_digest.json` | 人工／其他消費者 | `daily_digest.py` |
| `data/central_banks/raw/` | 90日彙整 | `backfill_central_bank_headlines.py` |
| `data/central_banks/latest_90d.json` | `daily_digest.py` | `backfill_central_bank_headlines.py` |
| `data/central_banks/digests/` | 前端／研究使用 | `daily_digest.py` |
| `data/web/latest.json` | `index.html` | `build_web_news.py` |
| `data/web/status.json` | 人工監控 | `build_web_news.py` |
| `central_bank_officials.json` | Backfill、央行 Digest | 人工維護 |
| `manual_central_bank_backfill.json` | Backfill | 人工維護 |

---

## 15. 結論

此專案的實際核心不是單一爬蟲，而是四層資料產品：

1. **原始新聞歷史層**：`data/archive`。
2. **可供模型處理的即時層**：`data/latest_24h.json` 與央行 `latest_90d.json`。
3. **AI 結構化摘要層**：`data/digests`、`data/central_banks/digests`。
4. **前端供應層**：`data/web/latest.json`。

整體資料路徑清楚，且已有原子寫入、狀態檔、API 重試、ID 白名單等保護。不過，未來最應優先改善的是跨 Workflow Push 競態、外部排程缺乏 Repo 內可追蹤性、央行前綴漏抓、Gemini 執行可重現性，以及 Git Repo 長期承載大量 JSON 所造成的規模問題。
