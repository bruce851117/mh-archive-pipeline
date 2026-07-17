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
DEBUG_DIRECTORY = Path("data/debug")
LATEST_DEBUG_FILE = Path("data/latest_digest_debug.json")

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"

REQUEST_TIMEOUT_SECONDS = 300
MAX_RETRIES = 3
MAX_OUTPUT_TOKENS = 65536
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
你是專業的全球金融市場新聞編輯，熟悉債券、股票、央行政策、政府財政、
總體經濟、戰爭與地緣政治。你會收到最近24小時的FinancialJuice英文市場Headline。
每筆資料只有id、time（台灣時間GMT+8）及headline。

請嚴格遵守：
- 每個事件只能分類為：債市、股市、央行、財政政策、經濟、戰爭。
- 使用台灣繁體中文；人名與重要專有名詞第一次出現時保留英文。不可補充輸入未提供的事實。
- 只輸出合法JSON，不要Markdown，不要額外說明。所有source_id只能使用輸入提供的id。

請盡量都把Headline留下來，不過以下我所提到的這些幫我刪除：
1.市場估計中國人民銀行可能將人民幣兌美元中間價設定在6.7734元。-->不會對市場造成影響
2.日本財務大臣片山皋月表示，不會評論特定匯率水準。-->除非他提到要干預，不然也不會對市場造成影響
3.台股跌幅超過2.7%。-->純講價格/漲跌幅 不重要
4.川普開始發表全國演說。-->沒有演說內容 不重要
5.白宮表示，情報顯示中國為這項新計畫專門成立一個資料利用單位。-->對市場沒有意義
6.中國商務部表示，將密切關注British Steel事件發展，並支持中國企業依法維護權益。-->對市場沒有意義
7.中國國家主席習近平表示，中國將人工智慧發展安全列為優先事項。-->中國官方說的話，除非提到關稅、貨幣政策及財政支出，都不會對市場造成影響
8.30日相關係數矩陣。、標普500主要成分股隱含波動率。、外匯隱含波動率。-->沒意義
9.亞塞拜然能源部表示，1至6月石油出口量為1,050萬噸。-->這種純報數據的 尤其是小國家 不需要
10.中國外匯管理局表示，上半年外資流入中國轉為淨流入，對外投資維持穩定成長。-->如果是中國的官方機構講的話，如果不是貨幣政策或關稅或財政政策或貿易政策，都刪掉
11.RBA Interest Rate Probabilities-->這個完全沒意義 又沒有數字


請對每一則留下的新聞評估重要性1至5分，並將分數填入 importance_score：

5分：可能立即且顯著影響全球主要股市、債市、外匯、商品、能源供應或主要央行政策預期。
4分：可能顯著影響主要國家、市場、資產類別、大型企業或重要政策。
3分：具有明確市場參考價值，但影響較局部，或市場影響仍待觀察。
2分：市場影響有限，但可作為重要事件的背景資訊。
1分：市場影響很小，但仍具有少量資訊價值。

被刪除的Headline不需要評分。
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
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TAIPEI_TIMEZONE)

    return parsed.astimezone(TAIPEI_TIMEZONE)


def read_json_list(file_path: Path) -> list[dict[str, Any]]:
    if not file_path.exists():
        raise FileNotFoundError(
            f"Input file does not exist: {file_path}"
        )

    try:
        with file_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"Invalid JSON in {file_path}: {error}"
        ) from error

    if not isinstance(data, list):
        raise RuntimeError(
            f"{file_path} must contain a JSON array."
        )

    return [item for item in data if isinstance(item, dict)]


def write_json(file_path: Path, data: Any) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = file_path.with_suffix(file_path.suffix + ".tmp")

    with temporary_file.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")

    temporary_file.replace(file_path)


def clean_source_item(
    item: dict[str, Any],
) -> dict[str, Any] | None:
    item_id = normalize_text(item.get("id"))
    headline = normalize_text(item.get("headline"))

    if not headline:
        headline = normalize_text(item.get("original_title"))

    published_datetime = parse_iso_datetime(
        item.get("published_at")
    )

    if not item_id or not headline or published_datetime is None:
        return None

    return {
        "id": item_id,
        "time": published_datetime.isoformat(),
        "headline": headline,
        "link": normalize_text(item.get("link")),
        "source": normalize_text(item.get("source"))
        or "FinancialJuice",
    }


def deduplicate_exact_items(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    unique_by_id: dict[str, dict[str, Any]] = {}

    for item in items:
        cleaned = clean_source_item(item)

        if cleaned is None:
            continue

        item_id = cleaned["id"]

        if item_id not in unique_by_id:
            unique_by_id[item_id] = cleaned

    earliest_by_headline: dict[str, dict[str, Any]] = {}

    for item in sorted(
        unique_by_id.values(),
        key=lambda row: row["time"],
    ):
        normalized_headline = re.sub(
            r"[^a-z0-9]+",
            " ",
            item["headline"].lower(),
        ).strip()

        if not normalized_headline:
            continue

        if normalized_headline not in earliest_by_headline:
            earliest_by_headline[normalized_headline] = item

    return sorted(
        earliest_by_headline.values(),
        key=lambda row: row["time"],
    )


def build_compact_input(
    items: list[dict[str, Any]],
) -> tuple[str, int]:
    compact_items: list[dict[str, str]] = []
    current_size = 2

    for item in items:
        compact_item = {
            "id": item["id"],
            "time": item["time"],
            "headline": item["headline"],
        }

        encoded = json.dumps(
            compact_item,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        projected_size = current_size + len(encoded) + 1

        if projected_size > MAX_INPUT_CHARACTERS:
            break

        compact_items.append(compact_item)
        current_size = projected_size

    return (
        json.dumps(
            compact_items,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        len(compact_items),
    )


def build_user_prompt(
    compact_input: str,
    input_count: int,
    included_count: int,
    period_start: str,
    period_end: str,
) -> str:
    return f"""
請整理以下最近24小時FinancialJuice市場Headline。
統計期間：{period_start} 至 {period_end}
時區：Asia/Taipei（GMT+8）
本地去除完全相同內容後：{input_count}則
實際送入模型：{included_count}則

請完成分類、語意去重、事件軌跡、繁體中文翻譯及重要性1至5分評估。
輸出必須符合以下JSON結構，六個分類均須出現：
{{
  "title":"最近24小時全球市場重要新聞",
  "period_start":"{period_start}",
  "period_end":"{period_end}",
  "timezone":"Asia/Taipei",
  "overview":"100至250個繁體中文字的市場主線",
  "categories":[
    {{
      "category":"債市",
      "news":[
        {{
          "importance_score":5,
          "headline_zh":"繁體中文事件標題",
          "summary_zh":"去重後摘要與市場意義",
          "event_time":"最早有效Headline時間",
          "source_id":"最早有效Headline ID",
          "trajectory":[
            {{
              "time":"後續時間",
              "source_id":"後續Headline ID",
              "update_type":"後續發展、修正、否認、推翻或正式確認",
              "description_zh":"新增或修正內容"
            }}
          ]
        }}
      ]
    }},
    {{"category":"股市","news":[]}},
    {{"category":"央行","news":[]}},
    {{"category":"財政政策","news":[]}},
    {{"category":"經濟","news":[]}},
    {{"category":"戰爭","news":[]}}
  ],
  "discarded":{{
    "estimated_count":0,
    "reasons":[]
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
            f"Prompt feedback: {response_data.get('promptFeedback', {})}"
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
            f"Finish reason: {first_candidate.get('finishReason', 'unknown')}"
        )

    return "\n".join(text_parts)


def parse_gemini_json(
    response_text: str,
) -> dict[str, Any]:
    cleaned = response_text.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(
            r"^```(?:json)?\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "Gemini returned invalid JSON: "
            f"{error}"
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
            "parts": [{"text": SYSTEM_INSTRUCTION}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
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
                response_text = get_response_text(response_data)
                result = parse_gemini_json(response_text)
                usage = response_data.get("usageMetadata", {})
                return result, usage

            response_text = response.text[:3000]

            if response.status_code not in {
                429,
                500,
                502,
                503,
                504,
            }:
                raise RuntimeError(
                    f"Gemini HTTP {response.status_code}: "
                    f"{response_text}"
                )

            last_error = RuntimeError(
                f"Gemini temporary HTTP {response.status_code}: "
                f"{response_text[:1000]}"
            )

        except (
            requests.RequestException,
            json.JSONDecodeError,
            RuntimeError,
        ) as error:
            last_error = error

        if attempt < MAX_RETRIES:
            wait_seconds = min(
                15 * (2 ** (attempt - 1)),
                60,
            )
            print(
                f"Gemini attempt {attempt} failed: "
                f"{last_error}; retrying in {wait_seconds}s",
                file=sys.stderr,
            )
            time.sleep(wait_seconds)

    raise RuntimeError(
        f"Gemini API failed after {MAX_RETRIES} attempts: "
        f"{last_error}"
    )


def normalize_score(value: Any) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 1


def normalize_digest(
    digest: dict[str, Any],
    source_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    category_map: dict[str, list[dict[str, Any]]] = {
        name: [] for name in ALLOWED_CATEGORIES
    }

    raw_categories = digest.get("categories", [])

    if isinstance(raw_categories, list):
        for raw_category in raw_categories:
            if not isinstance(raw_category, dict):
                continue

            category_name = normalize_text(
                raw_category.get("category")
            )

            if category_name not in category_map:
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
                source = source_lookup.get(source_id)

                if source is None:
                    continue

                trajectory: list[dict[str, Any]] = []
                seen_ids: set[str] = set()
                raw_trajectory = raw_item.get(
                    "trajectory",
                    [],
                )

                if isinstance(raw_trajectory, list):
                    for update in raw_trajectory:
                        if not isinstance(update, dict):
                            continue

                        update_id = normalize_text(
                            update.get("source_id")
                        )
                        update_source = source_lookup.get(
                            update_id
                        )

                        if (
                            not update_id
                            or update_id == source_id
                            or update_id in seen_ids
                            or update_source is None
                        ):
                            continue

                        seen_ids.add(update_id)
                        trajectory.append(
                            {
                                "time": update_source["time"],
                                "source_id": update_id,
                                "update_type": normalize_text(
                                    update.get("update_type")
                                ),
                                "description_zh": normalize_text(
                                    update.get("description_zh")
                                ),
                                "source_detail": update_source,
                            }
                        )

                trajectory.sort(
                    key=lambda row: row["time"]
                )

                category_map[category_name].append(
                    {
                        "importance_score": normalize_score(
                            raw_item.get("importance_score")
                        ),
                        "headline_zh": normalize_text(
                            raw_item.get("headline_zh")
                        ),
                        "summary_zh": normalize_text(
                            raw_item.get("summary_zh")
                        ),
                        "event_time": source["time"],
                        "source_id": source_id,
                        "source_detail": source,
                        "trajectory": trajectory,
                    }
                )

    normalized_categories: list[dict[str, Any]] = []

    for category_name in ALLOWED_CATEGORIES:
        news_items = category_map[category_name]

        news_items.sort(
            key=lambda row: row["event_time"],
            reverse=True,
        )
        news_items.sort(
            key=lambda row: row["importance_score"],
            reverse=True,
        )

        normalized_categories.append(
            {
                "category": category_name,
                "news": news_items,
            }
        )

    return {
        "title": normalize_text(digest.get("title"))
        or "最近24小時全球市場重要新聞",
        "period_start": normalize_text(
            digest.get("period_start")
        ),
        "period_end": normalize_text(
            digest.get("period_end")
        ),
        "timezone": "Asia/Taipei",
        "overview": normalize_text(digest.get("overview")),
        "categories": normalized_categories,
        "discarded": digest.get("discarded", {}),
    }


def build_selection_debug(
    raw_digest: dict[str, Any],
    source_lookup: dict[str, dict[str, Any]],
    run_at: datetime,
    model: str,
) -> dict[str, list[str]]:
    """
    Debug只列出Gemini最後刪除與留下的Headline。

    被選為主事件或放入trajectory的Headline視為留下；
    其他輸入Headline視為刪掉。
    """
    kept_ids: set[str] = set()
    raw_categories = raw_digest.get("categories", [])

    if isinstance(raw_categories, list):
        for category in raw_categories:
            if not isinstance(category, dict):
                continue

            news_items = category.get("news", [])

            if not isinstance(news_items, list):
                continue

            for news_item in news_items:
                if not isinstance(news_item, dict):
                    continue

                source_id = normalize_text(
                    news_item.get("source_id")
                )

                if source_id in source_lookup:
                    kept_ids.add(source_id)

                trajectory = news_item.get("trajectory", [])

                if not isinstance(trajectory, list):
                    continue

                for update in trajectory:
                    if not isinstance(update, dict):
                        continue

                    update_id = normalize_text(
                        update.get("source_id")
                    )

                    if update_id in source_lookup:
                        kept_ids.add(update_id)

    dropped_headlines: list[str] = []
    kept_headlines: list[str] = []

    sorted_sources = sorted(
        source_lookup.values(),
        key=lambda item: item.get("time", ""),
    )

    for source in sorted_sources:
        source_id = normalize_text(source.get("id"))
        headline = normalize_text(source.get("headline"))

        if not headline:
            continue

        if source_id in kept_ids:
            kept_headlines.append(headline)
        else:
            dropped_headlines.append(headline)

    return {
        "刪掉的headline": dropped_headlines,
        "留下的headline": kept_headlines,
    }


def count_news(digest: dict[str, Any]) -> int:
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
    status: str,
    run_at: datetime,
    model: str,
    input_count: int,
    included_count: int,
    output_count: int,
    usage: dict[str, Any] | None = None,
    error_message: str = "",
) -> None:
    status_data = {
        "status": status,
        "timezone": "Asia/Taipei",
        "utc_offset": "+08:00",
        "run_at": format_taipei_time(run_at),
        "model": model,
        "input_headline_count": input_count,
        "included_headline_count": included_count,
        "output_news_count": output_count,
        "usage_metadata": usage or {},
        "error": error_message,
    }

    if status == "success":
        status_data["last_success_at"] = format_taipei_time(
            run_at
        )

    write_json(STATUS_FILE, status_data)


def main() -> int:
    run_at = taipei_now()
    model = normalize_text(
        os.environ.get(
            "GEMINI_MODEL",
            DEFAULT_GEMINI_MODEL,
        )
    )
    api_key = normalize_text(
        os.environ.get("GEMINI_API_KEY")
    )

    if not api_key:
        print(
            "GEMINI_API_KEY is not configured.",
            file=sys.stderr,
        )
        return 1

    try:
        raw_items = read_json_list(INPUT_FILE)
        cleaned_items = deduplicate_exact_items(raw_items)

        if not cleaned_items:
            raise RuntimeError(
                "No valid headlines were found."
            )

        source_lookup = {
            item["id"]: item for item in cleaned_items
        }

        compact_input, included_count = build_compact_input(
            cleaned_items
        )

        period_end = format_taipei_time(run_at)
        period_start = format_taipei_time(
            run_at - timedelta(hours=24)
        )

        prompt = build_user_prompt(
            compact_input=compact_input,
            input_count=len(cleaned_items),
            included_count=included_count,
            period_start=period_start,
            period_end=period_end,
        )

        print(f"Input headlines: {len(cleaned_items)}")
        print(
            f"Headlines sent to Gemini: {included_count}"
        )
        print("Fields sent to Gemini: id, time, headline")
        print(f"Gemini model: {model}")

        raw_digest, usage = call_gemini(
            api_key=api_key,
            model=model,
            prompt=prompt,
        )

        debug_output = build_selection_debug(
            raw_digest=raw_digest,
            source_lookup=source_lookup,
            run_at=run_at,
            model=model,
        )

        digest = normalize_digest(
            raw_digest,
            source_lookup,
        )

        digest.update(
            {
                "period_start": period_start,
                "period_end": period_end,
                "generated_at": format_taipei_time(
                    run_at
                ),
                "model": model,
                "source": "FinancialJuice",
                "input_headline_count": len(
                    cleaned_items
                ),
                "included_headline_count": included_count,
                "usage_metadata": usage,
            }
        )

        output_count = count_news(digest)
        date_string = run_at.strftime("%Y-%m-%d")

        daily_digest_file = (
            DIGEST_DIRECTORY
            / run_at.strftime("%Y")
            / run_at.strftime("%m")
            / f"{date_string}.json"
        )

        debug_file = (
            DEBUG_DIRECTORY
            / run_at.strftime("%Y")
            / run_at.strftime("%m")
            / f"{date_string}.json"
        )

        write_json(daily_digest_file, digest)
        write_json(LATEST_DIGEST_FILE, digest)
        write_json(debug_file, debug_output)
        write_json(LATEST_DEBUG_FILE, debug_output)

        write_status(
            status="success",
            run_at=run_at,
            model=model,
            input_count=len(cleaned_items),
            included_count=included_count,
            output_count=output_count,
            usage=usage,
        )

        print(f"Final event count: {output_count}")
        print("")
        print("刪掉的headline：")

        for headline in debug_output["刪掉的headline"]:
            print(headline)

        print("")
        print("留下的headline：")

        for headline in debug_output["留下的headline"]:
            print(headline)

        print(f"Daily digest: {daily_digest_file}")
        print(f"Selection debug: {debug_file}")

        return 0

    except Exception as error:
        error_message = f"{type(error).__name__}: {error}"
        print(error_message, file=sys.stderr)

        try:
            write_status(
                status="failed",
                run_at=run_at,
                model=model,
                input_count=0,
                included_count=0,
                output_count=0,
                error_message=error_message,
            )
        except Exception as status_error:
            print(
                "Unable to write failure status: "
                f"{status_error}",
                file=sys.stderr,
            )

        return 1


if __name__ == "__main__":
    raise SystemExit(main())