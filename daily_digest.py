from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


TAIPEI_TIMEZONE = timezone(timedelta(hours=8))

INPUT_FILE = Path("data/latest_24h.json")
DIGEST_DIRECTORY = Path("data/digests")
LATEST_DIGEST_FILE = Path("data/latest_daily_digest.json")
STATUS_FILE = Path("data/digest_status.json")

GEMINI_API_BASE_URL = (
    "https://generativelanguage.googleapis.com/v1beta"
)

DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"

REQUEST_TIMEOUT_SECONDS = 300
MAX_RETRIES = 3
MAX_OUTPUT_TOKENS = 32768
MAX_INPUT_CHARACTERS = 500_000

ALLOWED_CATEGORIES = [
    "債市",
    "股市",
    "央行",
    "財政政策",
    "經濟",
    "戰爭",
]


SYSTEM_INSTRUCTION = """
你是專業的全球金融市場新聞編輯，熟悉債券、股票、央行政策、
政府財政、總體經濟、戰爭與地緣政治。

你會收到最近24小時的FinancialJuice英文市場Headline。

每筆資料只有：

- id：原始新聞唯一識別碼
- time：台灣時間GMT+8
- headline：英文Headline

你的任務是完成去重、分類、事件整理、重要性評估及繁體中文翻譯。

請嚴格遵守以下規則。

一、分類規則

每個事件只能放入以下一個主要分類：

1. 債市
2. 股市
3. 央行
4. 財政政策
5. 經濟
6. 戰爭

分類說明：

債市：
公債殖利率、利率期貨、殖利率曲線、國債發行、標售、信用利差、
公司債、信用評等、債券市場流動性及其他直接影響債市的消息。

股市：
主要股價指數、重要個股、企業財報、併購、資本支出、產業政策、
大型公司營運及可能明顯影響股票市場的消息。

央行：
央行官員談話、政策利率、升降息、資產負債表、量化寬鬆、
量化緊縮、通膨目標、金融環境及貨幣政策傳導。

財政政策：
政府支出、預算、稅收、財政刺激、關稅、補貼、產業支援、
政府融資、財政大臣或財長人選，以及潛在財政政策方向。

經濟：
通膨、就業、消費、製造業、服務業、房市、貿易、GDP、
企業活動、經濟預測及重要經濟數據。

戰爭：
戰爭、軍事攻擊、停火、制裁、封鎖、和平談判、能源運輸中斷、
航運安全及可能影響市場的重大地緣政治事件。

若新聞不適合以上任何分類，且沒有明確市場影響，請刪除。

二、重複事件處理

若相同事件或相同意思的Headline重複出現：

1. 刪除重複內容。
2. 只留下時間最早的那一則作為事件主要來源。
3. source_id必須填入最早那一則的id。
4. event_time必須使用最早那一則的time。
5. 不可因後面出現大量相似Headline而重複建立多則事件。

官員在同一場合反覆表達相同立場，也視為重複。

三、事件軌跡處理

若後續Headline提供真正的新資訊、修正、否認、升級或逆轉：

1. 不可刪除舊消息。
2. 將舊消息與新消息保留在同一事件的trajectory陣列。
3. trajectory必須依時間由早到晚排列。
4. 每個軌跡節點都要保留自己的source_id及time。
5. 說明該則消息是初始消息、後續發展、修正、否認或正式確認。
6. 不可因較新消息推翻舊消息，就假裝舊消息從未出現。
7. 最終摘要必須交代事件如何演變。

例如：

- 最初傳出可能停火。
- 隨後官員否認已達協議。
- 最後宣布確實開始談判。

以上三個階段都要保留，形成可追蹤的事件軌跡。

四、刪除低資訊內容

直接刪除以下類型：

1. Commodities Implied Volatility。
2. 90-Day Correlation Matrix。
3. 單純圖表名稱或指標名稱，但沒有任何數字與解釋。
4. WATCH LIVE或單純直播通知。
5. 某人開始或結束發言，但沒有實際內容。
6. 單純活動預告。
7. 無法理解其意義、缺乏主詞或缺乏事件內容的Headline。
8. 重複出現的市場價格或技術圖表名稱。
9. 廣告、產品宣傳、訂閱通知。
10. 缺乏市場意義的地方性或瑣碎新聞。

五、翻譯規則

1. 使用台灣繁體中文。
2. 人名及重要專有名詞第一次出現時保留英文。
3. 不可自行加入原始Headline沒有提供的事實。
4. 不可把推測寫成確定事實。
5. 數字、百分比、利率、貨幣與時間不可任意改動。
6. 翻譯需自然、專業、精簡。
7. 同一事件應整理成完整敘述，不可逐句機械翻譯。
8. 如果原始資訊不足，應保守表述。

六、重要性評分

每個保留事件給予importance_score，分數為1至5：

5分：
可能立即且顯著影響全球債券、股票、外匯、商品或主要央行政策預期。

4分：
可能顯著影響特定主要市場、國家、央行、產業或大型資產。

3分：
具有明確市場參考價值，但影響較局部或尚未完全明朗。

2分：
影響有限，但可作為事件背景或後續觀察資訊。

1分：
市場影響很小，但仍有少量資訊價值。

不明所以、無法理解、純圖表名稱、廣告與完全無市場價值的內容，
必須直接刪除，不可因為可以給1分就予以保留。

七、排序規則

1. 六大分類順序固定為：
   債市、股市、央行、財政政策、經濟、戰爭。
2. 每個分類內依importance_score由高至低排列。
3. 相同分數時，依event_time由新至舊排列。
4. trajectory內依時間由早至晚排列。

八、輸出規則

只輸出合法JSON。

不可使用Markdown程式碼區塊。

不可在JSON前後加入任何說明。

source_id及trajectory中的source_id只能使用輸入資料提供的id，
不可自行創造或修改。
""".strip()


def taipei_now() -> datetime:
    return datetime.now(TAIPEI_TIMEZONE)


def format_taipei_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=TAIPEI_TIMEZONE)

    return value.astimezone(TAIPEI_TIMEZONE).isoformat()


def normalize_text(value: Any) -> str:
    if value is None:
        return ""

    return " ".join(str(value).split()).strip()


def parse_iso_datetime(value: Any) -> datetime | None:
    text = normalize_text(value)

    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TAIPEI_TIMEZONE)

    return parsed.astimezone(TAIPEI_TIMEZONE)


def read_json_list(
    file_path: Path,
) -> list[dict[str, Any]]:
    if not file_path.exists():
        raise FileNotFoundError(
            f"Input file does not exist: {file_path}"
        )

    try:
        with file_path.open(
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"Invalid JSON in {file_path}: {error}"
        ) from error

    if not isinstance(data, list):
        raise RuntimeError(
            f"{file_path} must contain a JSON array."
        )

    return [
        item
        for item in data
        if isinstance(item, dict)
    ]


def write_json(
    file_path: Path,
    data: Any,
) -> None:
    file_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_file = file_path.with_suffix(
        file_path.suffix + ".tmp"
    )

    with temporary_file.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.write("\n")

    temporary_file.replace(file_path)


def clean_source_item(
    item: dict[str, Any],
) -> dict[str, Any] | None:
    item_id = normalize_text(item.get("id"))

    headline = normalize_text(
        item.get("headline")
    )

    if not headline:
        headline = normalize_text(
            item.get("original_title")
        )

    published_datetime = parse_iso_datetime(
        item.get("published_at")
    )

    if (
        not item_id
        or not headline
        or published_datetime is None
    ):
        return None

    return {
        "id": item_id,
        "time": published_datetime.isoformat(),
        "headline": headline,
        "link": normalize_text(item.get("link")),
        "source": normalize_text(
            item.get("source")
        ) or "FinancialJuice",
    }


def deduplicate_exact_items(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    本地僅刪除ID完全相同或Headline文字完全相同的資料。

    事件語意去重、修正與軌跡判斷交由Gemini處理。
    """
    unique_by_id: dict[str, dict[str, Any]] = {}
    earliest_by_headline: dict[str, dict[str, Any]] = {}

    for item in items:
        cleaned_item = clean_source_item(item)

        if cleaned_item is None:
            continue

        item_id = cleaned_item["id"]

        if item_id in unique_by_id:
            continue

        unique_by_id[item_id] = cleaned_item

    sorted_items = sorted(
        unique_by_id.values(),
        key=lambda item: item["time"],
    )

    for item in sorted_items:
        normalized_headline = re.sub(
            r"[^a-z0-9]+",
            " ",
            item["headline"].lower(),
        ).strip()

        if not normalized_headline:
            continue

        if normalized_headline not in earliest_by_headline:
            earliest_by_headline[
                normalized_headline
            ] = item

    return sorted(
        earliest_by_headline.values(),
        key=lambda item: item["time"],
    )


def build_source_lookup(
    items: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        item["id"]: item
        for item in items
    }


def build_compact_input(
    items: list[dict[str, Any]],
) -> tuple[str, int]:
    """
    送給Gemini的每筆資料只包含：

    - id
    - time
    - headline
    """
    compact_items: list[dict[str, str]] = []
    current_characters = 2

    for item in items:
        compact_item = {
            "id": item["id"],
            "time": item["time"],
            "headline": item["headline"],
        }

        compact_text = json.dumps(
            compact_item,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        projected_characters = (
            current_characters
            + len(compact_text)
            + 1
        )

        if projected_characters > MAX_INPUT_CHARACTERS:
            break

        compact_items.append(compact_item)
        current_characters = projected_characters

    compact_json = json.dumps(
        compact_items,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return compact_json, len(compact_items)


def build_user_prompt(
    compact_input: str,
    input_count: int,
    included_count: int,
    period_start: str,
    period_end: str,
) -> str:
    return f"""
請整理以下最近24小時FinancialJuice市場Headline。

統計期間：
{period_start} 至 {period_end}

時區：
Asia/Taipei（GMT+8）

本地去除完全相同內容後的Headline數：
{input_count}

本次實際送入模型的Headline數：
{included_count}

請執行：

1. 分成債市、股市、央行、財政政策、經濟、戰爭。
2. 判斷同一事件及相同意思的重複Headline。
3. 重複內容只保留時間最早的資料。
4. 真正的新發展、修正、否認、推翻或正式確認需保留為事件軌跡。
5. 刪除不明所以的圖表名稱、波動率矩陣、直播通知與低資訊內容。
6. 將保留事件整理及翻譯成台灣繁體中文。
7. 對每個事件給予1至5的重要性分數。
8. 如果事件沒有後續發展，trajectory可以是空陣列。
9. 如果事件有發展軌跡，trajectory不應重複主事件本身，
   只放後續真正新增、修正、否認或推翻的內容。
10. source_id必須使用代表該事件最早有效Headline的ID。

輸出以下JSON結構：

{{
  "title": "最近24小時全球市場重要新聞",
  "period_start": "{period_start}",
  "period_end": "{period_end}",
  "timezone": "Asia/Taipei",
  "overview": "用100至250個繁體中文字整理最近24小時的市場主線。",
  "categories": [
    {{
      "category": "債市",
      "news": [
        {{
          "importance_score": 5,
          "headline_zh": "繁體中文事件標題",
          "summary_zh": "合併去重後的繁體中文摘要，並交代事件與市場意義。",
          "event_time": "最早有效Headline的台灣時間",
          "source_id": "最早有效Headline的ID",
          "trajectory": [
            {{
              "time": "後續發展的台灣時間",
              "source_id": "後續Headline的ID",
              "update_type": "後續發展、修正、否認、推翻或正式確認",
              "description_zh": "此階段新增或修正了什麼"
            }}
          ]
        }}
      ]
    }},
    {{
      "category": "股市",
      "news": []
    }},
    {{
      "category": "央行",
      "news": []
    }},
    {{
      "category": "財政政策",
      "news": []
    }},
    {{
      "category": "經濟",
      "news": []
    }},
    {{
      "category": "戰爭",
      "news": []
    }}
  ],
  "discarded": {{
    "estimated_count": 0,
    "reasons": [
      "完全重複",
      "低資訊量",
      "不明圖表或矩陣名稱",
      "純直播通知",
      "不屬於六大分類且缺乏市場意義"
    ]
  }}
}}

輸入資料：

{compact_input}
""".strip()


def get_response_text(
    response_data: dict[str, Any],
) -> str:
    candidates = response_data.get("candidates")

    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError(
            "Gemini returned no candidates. "
            f"Prompt feedback: "
            f"{response_data.get('promptFeedback', {})}"
        )

    first_candidate = candidates[0]
    content = first_candidate.get("content", {})
    parts = content.get("parts", [])

    if not isinstance(parts, list):
        raise RuntimeError(
            "Gemini response contains no valid parts."
        )

    text_parts: list[str] = []

    for part in parts:
        if not isinstance(part, dict):
            continue

        text = part.get("text")

        if isinstance(text, str) and text.strip():
            text_parts.append(text.strip())

    if not text_parts:
        raise RuntimeError(
            "Gemini response did not contain text. "
            f"Finish reason: "
            f"{first_candidate.get('finishReason', 'unknown')}"
        )

    return "\n".join(text_parts)


def parse_gemini_json(
    response_text: str,
) -> dict[str, Any]:
    cleaned_text = response_text.strip()

    if cleaned_text.startswith("```"):
        cleaned_text = re.sub(
            r"^```(?:json)?\s*",
            "",
            cleaned_text,
            flags=re.IGNORECASE,
        )

        cleaned_text = re.sub(
            r"\s*```$",
            "",
            cleaned_text,
        )

    try:
        parsed = json.loads(cleaned_text)

    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"Gemini returned invalid JSON: {error}"
        ) from error

    if not isinstance(parsed, dict):
        raise RuntimeError(
            "Gemini output must be a JSON object."
        )

    return parsed


def call_gemini(
    api_key: str,
    model: str,
    prompt: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    endpoint = (
        f"{GEMINI_API_BASE_URL}/models/"
        f"{model}:generateContent"
    )

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
        "User-Agent": "mh-archive-pipeline/1.0",
    }

    request_body = {
        "systemInstruction": {
            "parts": [
                {
                    "text": SYSTEM_INSTRUCTION,
                }
            ]
        },
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": prompt,
                    }
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "topP": 0.9,
            "candidateCount": 1,
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
            "responseMimeType": "application/json",
        },
    }

    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=request_body,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            if response.status_code == 200:
                response_data = response.json()

                response_text = get_response_text(
                    response_data
                )

                parsed_result = parse_gemini_json(
                    response_text
                )

                usage_metadata = response_data.get(
                    "usageMetadata",
                    {},
                )

                return parsed_result, usage_metadata

            response_text = response.text[:3000]

            if response.status_code in {
                429,
                500,
                502,
                503,
                504,
            }:
                wait_seconds = min(
                    15 * (2 ** (attempt - 1)),
                    60,
                )

                print(
                    f"Gemini temporary error "
                    f"{response.status_code}; "
                    f"retrying in {wait_seconds} seconds.",
                    file=sys.stderr,
                )

                time.sleep(wait_seconds)
                continue

            raise RuntimeError(
                "Gemini API request failed with "
                f"HTTP {response.status_code}: "
                f"{response_text}"
            )

        except (
            requests.RequestException,
            json.JSONDecodeError,
            RuntimeError,
        ) as error:
            last_error = error

            if attempt >= MAX_RETRIES:
                break

            wait_seconds = min(
                15 * (2 ** (attempt - 1)),
                60,
            )

            print(
                f"Gemini request attempt {attempt} "
                f"failed: {error}",
                file=sys.stderr,
            )

            time.sleep(wait_seconds)

    raise RuntimeError(
        "Gemini API failed after "
        f"{MAX_RETRIES} attempts: {last_error}"
    )


def normalize_importance_score(
    value: Any,
) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return 1

    return max(1, min(5, score))


def enrich_source(
    source_id: str,
    source_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    source = source_lookup.get(source_id)

    if source is None:
        return None

    return {
        "id": source["id"],
        "time": source["time"],
        "headline": source["headline"],
        "link": source["link"],
        "source": source["source"],
    }


def normalize_digest(
    digest: dict[str, Any],
    source_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    raw_categories = digest.get("categories", [])

    category_lookup: dict[str, list[dict[str, Any]]] = {
        category: []
        for category in ALLOWED_CATEGORIES
    }

    if isinstance(raw_categories, list):
        for raw_category in raw_categories:
            if not isinstance(raw_category, dict):
                continue

            category_name = normalize_text(
                raw_category.get("category")
            )

            if category_name not in category_lookup:
                continue

            raw_news = raw_category.get("news", [])

            if not isinstance(raw_news, list):
                continue

            for raw_item in raw_news:
                if not isinstance(raw_item, dict):
                    continue

                source_id = normalize_text(
                    raw_item.get("source_id")
                )

                source_detail = enrich_source(
                    source_id,
                    source_lookup,
                )

                if source_detail is None:
                    continue

                trajectory_output: list[
                    dict[str, Any]
                ] = []

                raw_trajectory = raw_item.get(
                    "trajectory",
                    [],
                )

                if isinstance(raw_trajectory, list):
                    seen_trajectory_ids: set[str] = set()

                    for trajectory_item in raw_trajectory:
                        if not isinstance(
                            trajectory_item,
                            dict,
                        ):
                            continue

                        trajectory_source_id = (
                            normalize_text(
                                trajectory_item.get(
                                    "source_id"
                                )
                            )
                        )

                        if (
                            not trajectory_source_id
                            or trajectory_source_id
                            == source_id
                            or trajectory_source_id
                            in seen_trajectory_ids
                        ):
                            continue

                        trajectory_source = enrich_source(
                            trajectory_source_id,
                            source_lookup,
                        )

                        if trajectory_source is None:
                            continue

                        seen_trajectory_ids.add(
                            trajectory_source_id
                        )

                        trajectory_output.append(
                            {
                                "time": (
                                    trajectory_source["time"]
                                ),
                                "source_id": (
                                    trajectory_source_id
                                ),
                                "update_type": normalize_text(
                                    trajectory_item.get(
                                        "update_type"
                                    )
                                ),
                                "description_zh": (
                                    normalize_text(
                                        trajectory_item.get(
                                            "description_zh"
                                        )
                                    )
                                ),
                                "source_detail": (
                                    trajectory_source
                                ),
                            }
                        )

                trajectory_output.sort(
                    key=lambda item: item["time"]
                )

                category_lookup[
                    category_name
                ].append(
                    {
                        "importance_score": (
                            normalize_importance_score(
                                raw_item.get(
                                    "importance_score"
                                )
                            )
                        ),
                        "headline_zh": normalize_text(
                            raw_item.get(
                                "headline_zh"
                            )
                        ),
                        "summary_zh": normalize_text(
                            raw_item.get(
                                "summary_zh"
                            )
                        ),
                        "event_time": (
                            source_detail["time"]
                        ),
                        "source_id": source_id,
                        "source_detail": source_detail,
                        "trajectory": trajectory_output,
                    }
                )

    normalized_categories: list[
        dict[str, Any]
    ] = []

    for category_name in ALLOWED_CATEGORIES:
        news_items = category_lookup[category_name]

        news_items.sort(
            key=lambda item: (
                -item["importance_score"],
                item["event_time"],
            ),
            reverse=False,
        )

        normalized_categories.append(
            {
                "category": category_name,
                "news": news_items,
            }
        )

    return {
        "title": normalize_text(
            digest.get("title")
        ) or "最近24小時全球市場重要新聞",
        "period_start": normalize_text(
            digest.get("period_start")
        ),
        "period_end": normalize_text(
            digest.get("period_end")
        ),
        "timezone": "Asia/Taipei",
        "overview": normalize_text(
            digest.get("overview")
        ),
        "categories": normalized_categories,
        "discarded": digest.get(
            "discarded",
            {},
        ),
    }


def count_final_news(
    digest: dict[str, Any],
) -> int:
    total = 0

    for category in digest.get("categories", []):
        if not isinstance(category, dict):
            continue

        news_items = category.get("news", [])

        if isinstance(news_items, list):
            total += len(news_items)

    return total


def write_status(
    *,
*  *status* str,
    run_at: datetime,
*  *model* str,
*  *input*count: int,
*   included_count: int,
*  *output*count: int,
*  *usage_metadata: dict[str, Any] | N*ne = None,
*  *error*message: str = "",
**->*None:
   *status*data = {
*      *"*tatus": status,
*      *"timezone": "Asia/Taipei",
*      *"utc_offset": "+08:00",
*      *"run_at": format_taipei_time(run_a*),
       *"model": model,
*      *"*nput_headline_count": input_count,*       *"included_headline_count": include*_count,
       *"output_news_count": output_count,*       *"usage_metadata": usage_metadata o* {},
       *"error": error_message,
*  *}

*  *if*status == "success":
*      *status*data["last_success_at"] = (
*          *format_taipei_time(run_at)
*      *)

*  *write*json(STATUS_FILE, status_data)


*ef*main*) -> int:
*  *run*at = taipei_now()

   *model = normalize_text(
*      *os*environ.get(
*          *"GEMINI_MODEL",
*          *DEFAULT_GEMINI_MODEL,
*      *)
*  *)

*  *api*key = normalize_text(
       *os*environ.get("GEMINI_API_KEY")
*  *)

*  *if*not api_key:
*      *print*
*           "GEMINI_API_KEY is not *onfigured.",
*          *file=sys.stderr,
*      *)
*      *return*1

   *try*
*      *raw*items = read_json_list(INPUT_FILE)*
*      *cleaned_items = deduplicate_exact_*tems(
*          *raw*items
        )

        if not cl*aned_items:
            raise Runt*meError(
                "No valid*headlines were found."
           *)

        source_lookup = build_s*urce_lookup(
            cleaned_i*ems
        )

        compact_inp*t, included_count = (
            *uild_compact_input(cleaned_items)
*       )

        period_end_datet*me = run_at
        period_start_d*tetime = (
            period_end_*atetime - timedelta(hours=24)
    *   )

        period_start = forma*_taipei_time(
            period_s*art_datetime
        )

        pe*iod_end = format_taipei_time(
    *       period_end_datetime
       *)

        prompt = build_user_pro*pt(
            compact_input=comp*ct_input,
            input_count=*en(cleaned_items),
            inc*uded_count=included_count,
       *    period_start=period_start,
   *        period_end=period_end,
   *    )

        print(
            *"Input headlines: {len(cleaned_ite*s)}"
        )

        print(
   *        "Headlines sent to Gemini:*"
            f"{included_count}"
*       )

        print(
         *  "Fields sent to Gemini: "
      *     "id, time, headline"
        *

        print(f"Gemini model: {m*del}")

        raw_digest, usage_*etadata = call_gemini(
           *api_key=api_key,
            model*model,
            prompt=prompt,
*       )

        digest = normali*e_digest(
            raw_digest,
*           source_lookup,
        *

        digest["period_start"] =*period_start
        digest["period_end"] = period_end
        digest*"generated_at"] = (
            fo*mat_taipei_time(run_at)
        )
*       digest["model"] = model
   *    digest["source"] = "FinancialJ*ice"
        digest["input_headline_count"] = len(
            cleane*_items
        )
        digest["included_headline_count"] = (
      *     included_count
        )
    *   digest["usage_metadata"] = (
  *         usage_metadata
        )
*        output_count = count_final*news(digest)

        digest_date * run_at.strftime("%Y-%m-%d")

    *   daily_digest_file = (
         *  DIGEST_DIRECTORY
            / r*n_at.strftime("%Y")
            / *un_at.strftime("%m")
            /*f"{digest_date}.json"
        )

 *      write_json(
            dail*_digest_file,
            digest,
*       )

        write_json(
    *       LATEST_DIGEST_FILE,
       *    digest,
        )

        wri*e_status(
            status="succ*ss",
            run_at=run_at,
  *         model=model,
            *nput_count=len(cleaned_items),
   *        included_count=included_co*nt,
            output_count=outpu*_count,
            usage_metadata*usage_metadata,
        )

       *print(
            f"Final event c*unt: {output_count}"
        )

  *     print(
            f"Daily di*est: {daily_digest_file}"
        *

        print(
            f"Lat*st digest: {LATEST_DIGEST_FILE}"
 *      )

        return 0

    exc*pt Exception as error:
        err*r_message = (
            f"{type(*rror).__name__}: {error}"
        *

        print(
            error*message,
            file=sys.stde*r,
        )

        try:
       *    write_status(
                *tatus="failed",
                ru*_at=run_at,
                model=*odel,
                input_count=*,
                included_count=0*
                output_count=0,
 *              error_message=error_*essage,
            )

        exc*pt Exception as status_error:
    *       print(
                "Una*le to write failure status: "
    *           f"{status_error}",
    *           file=sys.stderr,
      *     )

        return 1


if __na*e__ == "__main__":
    raise Syste*Exit(main())