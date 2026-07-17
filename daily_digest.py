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
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
REQUEST_TIMEOUT_SECONDS = 300
MAX_RETRIES = 3
MAX_OUTPUT_TOKENS = 32768
MAX_INPUT_CHARACTERS = 500_000
ALLOWED_CATEGORIES = ["債市", "股市", "央行", "財政政策", "經濟", "戰爭"]

SYSTEM_INSTRUCTION = """
你是專業的全球金融市場新聞編輯，熟悉債券、股票、央行政策、政府財政、
總體經濟、戰爭與地緣政治。你會收到最近24小時的FinancialJuice英文市場Headline。
每筆資料只有id、time（台灣時間GMT+8）及headline。

請嚴格遵守：
1. 每個事件只能分類為：債市、股市、央行、財政政策、經濟、戰爭。
2. 相同事件或意思相同的重複Headline，只保留時間最早的一則；source_id與event_time均使用最早一則。
3. 若後續Headline提供真正的新資訊、修正、否認、推翻或正式確認，不可刪除舊消息；請放入同一事件的trajectory，依時間由早到晚排列，呈現事情軌跡。
4. 刪除Commodities Implied Volatility、90-Day Correlation Matrix、純圖表或指標名稱、WATCH LIVE、發言開始或結束通知、活動預告、廣告、無法理解或缺乏市場意義的內容。
5. 使用台灣繁體中文；人名與重要專有名詞第一次出現時保留英文。不可補充輸入未提供的事實。
6. 每個保留事件評為1至5分：5為可能立即顯著影響全球主要市場或政策預期；4為顯著影響主要市場或國家；3為有明確市場參考價值；2為影響有限但具背景價值；1為影響很小但仍有少量資訊價值。
7. 分類固定順序：債市、股市、央行、財政政策、經濟、戰爭。分類內依重要性由高至低；同分時依event_time由新至舊。
8. 只輸出合法JSON，不要Markdown，不要額外說明。所有source_id只能使用輸入提供的id。
9. 必須對每一個輸入ID在selection_audit中交代去留：decision只能是keep或drop。keep代表該ID被用作事件主來源或事件軌跡；drop代表未保留。
10. 若drop是因為與另一事件重複，mapped_event_source_id填入被保留事件最早Headline的ID；其餘情況填空字串。reason_zh需簡短說明原因。
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
        raise FileNotFoundError(f"Input file does not exist: {file_path}")
    with file_path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise RuntimeError(f"{file_path} must contain a JSON array.")
    return [item for item in data if isinstance(item, dict)]


def write_json(file_path: Path, data: Any) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = file_path.with_suffix(file_path.suffix + ".tmp")
    with temporary_file.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temporary_file.replace(file_path)


def clean_source_item(item: dict[str, Any]) -> dict[str, Any] | None:
    item_id = normalize_text(item.get("id"))
    headline = normalize_text(item.get("headline")) or normalize_text(item.get("original_title"))
    published_datetime = parse_iso_datetime(item.get("published_at"))
    if not item_id or not headline or published_datetime is None:
        return None
    return {
        "id": item_id,
        "time": published_datetime.isoformat(),
        "headline": headline,
        "link": normalize_text(item.get("link")),
        "source": normalize_text(item.get("source")) or "FinancialJuice",
    }


def deduplicate_exact_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique_by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        cleaned = clean_source_item(item)
        if cleaned is not None and cleaned["id"] not in unique_by_id:
            unique_by_id[cleaned["id"]] = cleaned

    earliest_by_headline: dict[str, dict[str, Any]] = {}
    for item in sorted(unique_by_id.values(), key=lambda row: row["time"]):
        key = re.sub(r"[^a-z0-9]+", " ", item["headline"].lower()).strip()
        if key and key not in earliest_by_headline:
            earliest_by_headline[key] = item
    return sorted(earliest_by_headline.values(), key=lambda row: row["time"])


def build_compact_input(items: list[dict[str, Any]]) -> tuple[str, int]:
    compact_items: list[dict[str, str]] = []
    current_size = 2
    for item in items:
        compact = {"id": item["id"], "time": item["time"], "headline": item["headline"]}
        encoded = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        if current_size + len(encoded) + 1 > MAX_INPUT_CHARACTERS:
            break
        compact_items.append(compact)
        current_size += len(encoded) + 1
    return json.dumps(compact_items, ensure_ascii=False, separators=(",", ":")), len(compact_items)


def build_user_prompt(compact_input: str, input_count: int, included_count: int, period_start: str, period_end: str) -> str:
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
    {{"category":"債市","news":[{{
      "importance_score":5,
      "headline_zh":"繁體中文事件標題",
      "summary_zh":"去重後摘要與市場意義",
      "event_time":"最早有效Headline時間",
      "source_id":"最早有效Headline ID",
      "trajectory":[{{
        "time":"後續時間",
        "source_id":"後續Headline ID",
        "update_type":"後續發展、修正、否認、推翻或正式確認",
        "description_zh":"新增或修正內容"
      }}]
    }}]}},
    {{"category":"股市","news":[]}},
    {{"category":"央行","news":[]}},
    {{"category":"財政政策","news":[]}},
    {{"category":"經濟","news":[]}},
    {{"category":"戰爭","news":[]}}
  ],
  "discarded":{{"estimated_count":0,"reasons":[]}},
  "selection_audit":[
    {{
      "id":"輸入資料中的ID",
      "decision":"keep或drop",
      "reason_zh":"保留或刪除原因",
      "mapped_event_source_id":"若因重複而刪除，填入被保留事件的source_id，否則空字串"
    }}
  ]
}}

輸入資料：
{compact_input}
""".strip()


def get_response_text(response_data: dict[str, Any]) -> str:
    candidates = response_data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {response_data.get('promptFeedback', {})}")
    parts = candidates[0].get("content", {}).get("parts", [])
    texts = [part.get("text", "").strip() for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str) and part.get("text", "").strip()]
    if not texts:
        raise RuntimeError(f"Gemini response contained no text. Finish reason: {candidates[0].get('finishReason', 'unknown')}")
    return "\n".join(texts)


def parse_gemini_json(response_text: str) -> dict[str, Any]:
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise RuntimeError("Gemini output must be a JSON object.")
    return parsed


def call_gemini(api_key: str, model: str, prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
    endpoint = f"{GEMINI_API_BASE_URL}/models/{model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
        "User-Agent": "mh-archive-pipeline/1.0",
    }
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
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
            response = requests.post(endpoint, headers=headers, json=body, timeout=REQUEST_TIMEOUT_SECONDS)
            if response.status_code == 200:
                response_data = response.json()
                return parse_gemini_json(get_response_text(response_data)), response_data.get("usageMetadata", {})
            if response.status_code not in {429, 500, 502, 503, 504}:
                raise RuntimeError(f"Gemini HTTP {response.status_code}: {response.text[:3000]}")
            last_error = RuntimeError(f"Gemini temporary HTTP {response.status_code}: {response.text[:1000]}")
        except (requests.RequestException, json.JSONDecodeError, RuntimeError) as error:
            last_error = error
        if attempt < MAX_RETRIES:
            wait_seconds = min(15 * (2 ** (attempt - 1)), 60)
            print(f"Gemini attempt {attempt} failed: {last_error}; retrying in {wait_seconds}s", file=sys.stderr)
            time.sleep(wait_seconds)
    raise RuntimeError(f"Gemini API failed after {MAX_RETRIES} attempts: {last_error}")


def normalize_score(value: Any) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 1


def normalize_digest(digest: dict[str, Any], source_lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    category_map: dict[str, list[dict[str, Any]]] = {name: [] for name in ALLOWED_CATEGORIES}
    raw_categories = digest.get("categories", [])
    if isinstance(raw_categories, list):
        for raw_category in raw_categories:
            if not isinstance(raw_category, dict):
                continue
            name = normalize_text(raw_category.get("category"))
            if name not in category_map or not isinstance(raw_category.get("news"), list):
                continue
            for raw_item in raw_category["news"]:
                if not isinstance(raw_item, dict):
                    continue
                source_id = normalize_text(raw_item.get("source_id"))
                source = source_lookup.get(source_id)
                if source is None:
                    continue
                trajectory: list[dict[str, Any]] = []
                seen_ids: set[str] = set()
                raw_trajectory = raw_item.get("trajectory", [])
                if isinstance(raw_trajectory, list):
                    for update in raw_trajectory:
                        if not isinstance(update, dict):
                            continue
                        update_id = normalize_text(update.get("source_id"))
                        update_source = source_lookup.get(update_id)
                        if not update_id or update_id == source_id or update_id in seen_ids or update_source is None:
                            continue
                        seen_ids.add(update_id)
                        trajectory.append({
                            "time": update_source["time"],
                            "source_id": update_id,
                            "update_type": normalize_text(update.get("update_type")),
                            "description_zh": normalize_text(update.get("description_zh")),
                            "source_detail": update_source,
                        })
                trajectory.sort(key=lambda row: row["time"])
                category_map[name].append({
                    "importance_score": normalize_score(raw_item.get("importance_score")),
                    "headline_zh": normalize_text(raw_item.get("headline_zh")),
                    "summary_zh": normalize_text(raw_item.get("summary_zh")),
                    "event_time": source["time"],
                    "source_id": source_id,
                    "source_detail": source,
                    "trajectory": trajectory,
                })

    output_categories: list[dict[str, Any]] = []
    for name in ALLOWED_CATEGORIES:
        news = category_map[name]
        news.sort(key=lambda row: row["event_time"], reverse=True)
        news.sort(key=lambda row: row["importance_score"], reverse=True)
        output_categories.append({"category": name, "news": news})

    return {
        "title": normalize_text(digest.get("title")) or "最近24小時全球市場重要新聞",
        "period_start": normalize_text(digest.get("period_start")),
        "period_end": normalize_text(digest.get("period_end")),
        "timezone": "Asia/Taipei",
        "overview": normalize_text(digest.get("overview")),
        "categories": output_categories,
        "discarded": digest.get("discarded", {}),
    }


def build_selection_debug(
    raw_digest: dict[str, Any],
    source_lookup: dict[str, dict[str, Any]],
    run_at: datetime,
    model: str,
) -> dict[str, Any]:
    audit_lookup: dict[str, dict[str, str]] = {}
    raw_audit = raw_digest.get("selection_audit", [])

    if isinstance(raw_audit, list):
        for row in raw_audit:
            if not isinstance(row, dict):
                continue
            item_id = normalize_text(row.get("id"))
            if item_id not in source_lookup or item_id in audit_lookup:
                continue
            decision = normalize_text(row.get("decision")).lower()
            if decision not in {"keep", "drop"}:
                continue
            audit_lookup[item_id] = {
                "decision": decision,
                "reason_zh": normalize_text(row.get("reason_zh")),
                "mapped_event_source_id": normalize_text(
                    row.get("mapped_event_source_id")
                ),
            }

    referenced_ids: set[str] = set()
    for category in raw_digest.get("categories", []):
        if not isinstance(category, dict):
            continue
        for news_item in category.get("news", []):
            if not isinstance(news_item, dict):
                continue
            source_id = normalize_text(news_item.get("source_id"))
            if source_id in source_lookup:
                referenced_ids.add(source_id)
            for update in news_item.get("trajectory", []):
                if isinstance(update, dict):
                    update_id = normalize_text(update.get("source_id"))
                    if update_id in source_lookup:
                        referenced_ids.add(update_id)

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    for item_id, source in source_lookup.items():
        audit = audit_lookup.get(item_id, {})
        is_referenced = item_id in referenced_ids
        decision = "keep" if is_referenced else "drop"
        model_decision = audit.get("decision", "")

        if model_decision and model_decision != decision:
            reason = (
                f"模型稽核標示為{model_decision}，但以最終輸出ID引用結果校正為{decision}。"
            )
        else:
            reason = audit.get("reason_zh", "")

        if not reason:
            reason = (
                "保留為事件主來源或事件軌跡。"
                if decision == "keep"
                else "未被Gemini最終事件或軌跡引用。"
            )

        output_row = {
            "id": item_id,
            "time": source["time"],
            "headline": source["headline"],
            "decision": decision,
            "reason_zh": reason,
            "mapped_event_source_id": audit.get(
                "mapped_event_source_id", ""
            ),
        }

        if decision == "keep":
            kept.append(output_row)
        else:
            dropped.append(output_row)

    kept.sort(key=lambda row: row["time"])
    dropped.sort(key=lambda row: row["time"])

    return {
        "generated_at": format_taipei_time(run_at),
        "model": model,
        "input_count": len(source_lookup),
        "kept_count": len(kept),
        "dropped_count": len(dropped),
        "kept_headlines": kept,
        "dropped_headlines": dropped,
    }


def count_news(digest: dict[str, Any]) -> int:
    return sum(len(category.get("news", [])) for category in digest.get("categories", []) if isinstance(category, dict) and isinstance(category.get("news"), list))


def write_status(status: str, run_at: datetime, model: str, input_count: int, included_count: int, output_count: int, usage: dict[str, Any] | None = None, error: str = "") -> None:
    data = {
        "status": status,
        "timezone": "Asia/Taipei",
        "utc_offset": "+08:00",
        "run_at": format_taipei_time(run_at),
        "model": model,
        "input_headline_count": input_count,
        "included_headline_count": included_count,
        "output_news_count": output_count,
        "usage_metadata": usage or {},
        "error": error,
    }
    if status == "success":
        data["last_success_at"] = format_taipei_time(run_at)
    write_json(STATUS_FILE, data)


def main() -> int:
    run_at = taipei_now()
    model = normalize_text(os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL))
    api_key = normalize_text(os.environ.get("GEMINI_API_KEY"))
    if not api_key:
        print("GEMINI_API_KEY is not configured.", file=sys.stderr)
        return 1

    try:
        cleaned_items = deduplicate_exact_items(read_json_list(INPUT_FILE))
        if not cleaned_items:
            raise RuntimeError("No valid headlines were found.")
        source_lookup = {item["id"]: item for item in cleaned_items}
        compact_input, included_count = build_compact_input(cleaned_items)
        period_end = format_taipei_time(run_at)
        period_start = format_taipei_time(run_at - timedelta(hours=24))
        prompt = build_user_prompt(compact_input, len(cleaned_items), included_count, period_start, period_end)

        print(f"Input headlines: {len(cleaned_items)}")
        print(f"Headlines sent to Gemini: {included_count}")
        print("Fields sent to Gemini: id, time, headline")
        print(f"Gemini model: {model}")

        raw_digest, usage = call_gemini(api_key, model, prompt)
        debug_output = build_selection_debug(
            raw_digest, source_lookup, run_at, model
        )
        digest = normalize_digest(raw_digest, source_lookup)
        digest.update({
            "period_start": period_start,
            "period_end": period_end,
            "generated_at": format_taipei_time(run_at),
            "model": model,
            "source": "FinancialJuice",
            "input_headline_count": len(cleaned_items),
            "included_headline_count": included_count,
            "usage_metadata": usage,
        })
        output_count = count_news(digest)
        daily_file = DIGEST_DIRECTORY / run_at.strftime("%Y") / run_at.strftime("%m") / f"{run_at.strftime('%Y-%m-%d')}.json"
        debug_file = DEBUG_DIRECTORY / run_at.strftime("%Y") / run_at.strftime("%m") / f"{run_at.strftime('%Y-%m-%d')}.json"
        write_json(daily_file, digest)
        write_json(LATEST_DIGEST_FILE, digest)
        write_json(debug_file, debug_output)
        write_json(LATEST_DEBUG_FILE, debug_output)
        write_status("success", run_at, model, len(cleaned_items), included_count, output_count, usage)
        print(f"Final event count: {output_count}")
        print(f"Kept headline count: {debug_output['kept_count']}")
        print(f"Dropped headline count: {debug_output['dropped_count']}")
        print(f"Daily digest: {daily_file}")
        print(f"Selection debug: {debug_file}")
        return 0
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        print(message, file=sys.stderr)
        try:
            write_status("failed", run_at, model, 0, 0, 0, error=message)
        except Exception as status_error:
            print(f"Unable to write failure status: {status_error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
