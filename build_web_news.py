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
DIGEST_DIRECTORY = Path("data/digests")
WEB_DIRECTORY = Path("data/web")
WEB_OUTPUT_FILE = WEB_DIRECTORY / "latest.json"
WEB_STATUS_FILE = WEB_DIRECTORY / "status.json"

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
REQUEST_TIMEOUT_SECONDS = 300
MAX_RETRIES = 3
MAX_OUTPUT_TOKENS = 8192

CATEGORY_ORDER = [
    "債市",
    "股市",
    "央行",
    "財政政策",
    "經濟",
    "戰爭",
]

SUMMARY_SYSTEM_INSTRUCTION = """
你是專業的全球金融市場晨報編輯。
你會收到一段指定期間內已完成分類、去重、翻譯及重要性評分的市場事件。

請從輸入事件中選出最重要的5至10點，整理成適合放在網頁最上方的繁體中文重點摘要。

規則：
1. 只能根據輸入資料，不可補充外部資訊。
2. 優先選擇重要性5分及4分事件，再考量3分事件。
3. 摘要應涵蓋影響最大的央行、債市、財政政策、經濟、股市與戰爭消息。
4. 相同事件不得重複列點。
5. 每點應為一至兩句完整文字，說明事件及其市場意義。
6. 若重要事件少於5件，可少於5點；不得為湊數加入不重要內容。
7. 最多10點。
8. 使用台灣繁體中文。
9. 只輸出合法JSON，不要Markdown，不要額外說明。
""".strip()


def taipei_now() -> datetime:
    return datetime.now(TAIPEI_TIMEZONE)


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def parse_datetime(value: Any) -> datetime | None:
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


def format_time(value: datetime) -> str:
    return value.astimezone(TAIPEI_TIMEZONE).isoformat()


def calculate_display_window(now: datetime) -> tuple[datetime, datetime]:
    now = now.astimezone(TAIPEI_TIMEZONE)
    weekday = now.weekday()

    if weekday in {1, 2, 3, 4}:
        start_date = (now - timedelta(days=1)).date()
    else:
        days_since_friday = (weekday - 4) % 7
        start_date = (now - timedelta(days=days_since_friday)).date()

    start = datetime(
        start_date.year,
        start_date.month,
        start_date.day,
        17,
        0,
        0,
        tzinfo=TAIPEI_TIMEZONE,
    )

    return start, now


def read_json(file_path: Path) -> dict[str, Any]:
    with file_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise RuntimeError(f"{file_path} must contain a JSON object.")

    return data


def write_json(file_path: Path, data: Any) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = file_path.with_suffix(file_path.suffix + ".tmp")

    with temporary_file.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")

    temporary_file.replace(file_path)


def find_digest_files(start: datetime, end: datetime) -> list[Path]:
    candidate_dates: set[str] = set()
    current_date = (start.date() - timedelta(days=1))
    final_date = end.date()

    while current_date <= final_date:
        candidate_dates.add(current_date.isoformat())
        current_date += timedelta(days=1)

    files: list[Path] = []

    for date_string in sorted(candidate_dates):
        parsed = datetime.strptime(date_string, "%Y-%m-%d")
        file_path = (
            DIGEST_DIRECTORY
            / parsed.strftime("%Y")
            / parsed.strftime("%m")
            / f"{date_string}.json"
        )

        if file_path.exists():
            files.append(file_path)

    return files


def event_effective_time(event: dict[str, Any]) -> datetime | None:
    return parse_datetime(event.get("event_time"))


def event_is_in_window(
    event: dict[str, Any],
    start: datetime,
    end: datetime,
) -> bool:
    event_time = event_effective_time(event)

    if event_time is not None and start <= event_time <= end:
        return True

    trajectory = event.get("trajectory", [])

    if isinstance(trajectory, list):
        for update in trajectory:
            if not isinstance(update, dict):
                continue

            update_time = parse_datetime(update.get("time"))

            if update_time is not None and start <= update_time <= end:
                return True

    return False


def normalize_event(
    event: dict[str, Any],
    category: str,
) -> dict[str, Any] | None:
    source_id = normalize_text(event.get("source_id"))
    headline_zh = normalize_text(event.get("headline_zh"))
    summary_zh = normalize_text(event.get("summary_zh"))
    event_time = event_effective_time(event)

    if not source_id or not headline_zh or event_time is None:
        return None

    try:
        importance = int(event.get("importance_score", 1))
    except (TypeError, ValueError):
        importance = 1

    importance = max(1, min(5, importance))

    trajectory_output: list[dict[str, Any]] = []
    raw_trajectory = event.get("trajectory", [])

    if isinstance(raw_trajectory, list):
        for update in raw_trajectory:
            if not isinstance(update, dict):
                continue

            update_time = parse_datetime(update.get("time"))

            if update_time is None:
                continue

            trajectory_output.append(
                {
                    "time": format_time(update_time),
                    "update_type": normalize_text(update.get("update_type")),
                    "description_zh": normalize_text(
                        update.get("description_zh")
                    ),
                }
            )

    trajectory_output.sort(key=lambda row: row["time"])

    return {
        "source_id": source_id,
        "category": category,
        "importance_score": importance,
        "highlight": importance >= 4,
        "headline_zh": headline_zh,
        "summary_zh": summary_zh,
        "event_time": format_time(event_time),
        "trajectory": trajectory_output,
    }


def load_window_events(
    digest_files: list[Path],
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    events_by_id: dict[str, dict[str, Any]] = {}

    for file_path in digest_files:
        digest = read_json(file_path)
        categories = digest.get("categories", [])

        if not isinstance(categories, list):
            continue

        for category_block in categories:
            if not isinstance(category_block, dict):
                continue

            category = normalize_text(category_block.get("category"))

            if category not in CATEGORY_ORDER:
                continue

            news_items = category_block.get("news", [])

            if not isinstance(news_items, list):
                continue

            for event in news_items:
                if not isinstance(event, dict):
                    continue

                if not event_is_in_window(event, start, end):
                    continue

                normalized = normalize_event(event, category)

                if normalized is None:
                    continue

                source_id = normalized["source_id"]
                existing = events_by_id.get(source_id)

                if existing is None:
                    events_by_id[source_id] = normalized
                    continue

                if normalized["importance_score"] > existing["importance_score"]:
                    events_by_id[source_id] = normalized

    events = list(events_by_id.values())
    events.sort(key=lambda row: row["event_time"], reverse=True)
    events.sort(key=lambda row: row["importance_score"], reverse=True)
    return events


def build_summary_input(events: list[dict[str, Any]]) -> str:
    compact_events = [
        {
            "source_id": event["source_id"],
            "category": event["category"],
            "importance_score": event["importance_score"],
            "headline_zh": event["headline_zh"],
            "summary_zh": event["summary_zh"],
            "event_time": event["event_time"],
        }
        for event in events
    ]

    return json.dumps(
        compact_events,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def get_response_text(response_data: dict[str, Any]) -> str:
    candidates = response_data.get("candidates")

    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError(
            f"Gemini returned no candidates: "
            f"{response_data.get('promptFeedback', {})}"
        )

    parts = candidates[0].get("content", {}).get("parts", [])
    texts: list[str] = []

    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue

            text = part.get("text")

            if isinstance(text, str) and text.strip():
                texts.append(text.strip())

    if not texts:
        raise RuntimeError("Gemini response contained no text.")

    return "\n".join(texts)


def parse_json_response(response_text: str) -> dict[str, Any]:
    cleaned = response_text.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(
            r"^```(?:json)?\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s*```$", "", cleaned)

    parsed = json.loads(cleaned)

    if not isinstance(parsed, dict):
        raise RuntimeError("Gemini summary output must be a JSON object.")

    return parsed


def call_gemini_summary(
    api_key: str,
    model: str,
    events: list[dict[str, Any]],
    start: datetime,
    end: datetime,
) -> tuple[list[str], dict[str, Any]]:
    endpoint = (
        f"{GEMINI_API_BASE_URL}/models/"
        f"{model}:generateContent"
    )

    prompt = f"""
請整理以下市場事件，產生5至10點最重要摘要。

統計期間：{format_time(start)} 至 {format_time(end)}
時區：Asia/Taipei（GMT+8）
事件數：{len(events)}

輸出格式：
{{
  "summary_points": [
    "重點一",
    "重點二"
  ]
}}

輸入事件：
{build_summary_input(events)}
""".strip()

    request_body = {
        "systemInstruction": {
            "parts": [{"text": SUMMARY_SYSTEM_INSTRUCTION}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "candidateCount": 1,
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
            "responseMimeType": "application/json",
        },
    }

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
        "User-Agent": "mh-archive-pipeline-web/1.0",
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
                parsed = parse_json_response(
                    get_response_text(response_data)
                )
                raw_points = parsed.get("summary_points", [])

                if not isinstance(raw_points, list):
                    raise RuntimeError(
                        "Gemini summary_points must be an array."
                    )

                points = [
                    normalize_text(point)
                    for point in raw_points
                    if normalize_text(point)
                ][:10]

                return points, response_data.get("usageMetadata", {})

            if response.status_code not in {429, 500, 502, 503, 504}:
                raise RuntimeError(
                    f"Gemini HTTP {response.status_code}: "
                    f"{response.text[:2000]}"
                )

            last_error = RuntimeError(
                f"Temporary Gemini HTTP {response.status_code}"
            )

        except (
            requests.RequestException,
            json.JSONDecodeError,
            RuntimeError,
        ) as error:
            last_error = error

        if attempt < MAX_RETRIES:
            wait_seconds = min(15 * (2 ** (attempt - 1)), 60)
            print(
                f"Gemini summary attempt {attempt} failed: "
                f"{last_error}; retrying in {wait_seconds}s",
                file=sys.stderr,
            )
            time.sleep(wait_seconds)

    raise RuntimeError(
        f"Gemini summary failed after {MAX_RETRIES} attempts: "
        f"{last_error}"
    )


def group_blocks(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []

    for category in CATEGORY_ORDER:
        category_events = [
            event for event in events if event["category"] == category
        ]

        category_events.sort(
            key=lambda row: row["event_time"],
            reverse=True,
        )
        category_events.sort(
            key=lambda row: row["importance_score"],
            reverse=True,
        )

        blocks.append(
            {
                "category": category,
                "news": category_events,
            }
        )

    return blocks


def main() -> int:
    run_at = taipei_now()
    api_key = normalize_text(os.environ.get("GEMINI_API_KEY"))
    model = normalize_text(
        os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    )

    if not api_key:
        print("GEMINI_API_KEY is not configured.", file=sys.stderr)
        return 1

    try:
        period_start, period_end = calculate_display_window(run_at)
        digest_files = find_digest_files(period_start, period_end)

        if not digest_files:
            raise RuntimeError(
                "No digest files were found for the display period."
            )

        events = load_window_events(
            digest_files,
            period_start,
            period_end,
        )

        if not events:
            raise RuntimeError(
                "No digest events were found in the display period."
            )

        summary_points, usage = call_gemini_summary(
            api_key,
            model,
            events,
            period_start,
            period_end,
        )

        output = {
            "generated_at": format_time(run_at),
            "timezone": "Asia/Taipei",
            "period_start": format_time(period_start),
            "period_end": format_time(period_end),
            "model": model,
            "source_digest_files": [
                str(file_path) for file_path in digest_files
            ],
            "event_count": len(events),
            "summary_points": summary_points,
            "blocks": group_blocks(events),
            "usage_metadata": usage,
        }

        write_json(WEB_OUTPUT_FILE, output)
        write_json(
            WEB_STATUS_FILE,
            {
                "status": "success",
                "generated_at": format_time(run_at),
                "period_start": format_time(period_start),
                "period_end": format_time(period_end),
                "event_count": len(events),
                "summary_count": len(summary_points),
                "model": model,
                "error": "",
            },
        )

        print(f"Display period: {period_start} to {period_end}")
        print(f"Digest files: {len(digest_files)}")
        print(f"Web events: {len(events)}")
        print(f"Summary points: {len(summary_points)}")
        print(f"Output: {WEB_OUTPUT_FILE}")
        return 0

    except Exception as error:
        error_message = f"{type(error).__name__}: {error}"
        print(error_message, file=sys.stderr)

        try:
            write_json(
                WEB_STATUS_FILE,
                {
                    "status": "failed",
                    "generated_at": format_time(run_at),
                    "model": model,
                    "error": error_message,
                },
            )
        except Exception as status_error:
            print(
                f"Unable to write web status: {status_error}",
                file=sys.stderr,
            )

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
