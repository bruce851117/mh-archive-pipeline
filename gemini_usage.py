"""集中記錄每一次Gemini呼叫的token用量，方便回頭追成本。

每天一個檔案：data/usage/YYYY/MM/YYYY-MM-DD.json
最新一天另存 data/usage/latest.json，逐日總計存 data/usage/history.json。

thinking token在Gemini是以output計價，所以另外算一個
billed_output_tokens = output_tokens + thinking_tokens 方便對帳。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TAIPEI_TIMEZONE = timezone(timedelta(hours=8))

USAGE_DIRECTORY = Path("data/usage")
USAGE_LATEST_FILE = USAGE_DIRECTORY / "latest.json"
USAGE_HISTORY_FILE = USAGE_DIRECTORY / "history.json"
HISTORY_MAX_DAYS = 400

# 內部欄位名稱 -> Gemini usageMetadata 欄位名稱
USAGE_FIELDS = (
    ("input_tokens", "promptTokenCount"),
    ("output_tokens", "candidatesTokenCount"),
    ("thinking_tokens", "thoughtsTokenCount"),
    ("cached_input_tokens", "cachedContentTokenCount"),
    ("total_tokens", "totalTokenCount"),
)

COUNTED_FIELDS = tuple(name for name, _ in USAGE_FIELDS) + (
    "billed_output_tokens",
)


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _read_json(file_path: Path) -> Any:
    if not file_path.exists():
        return None

    try:
        with file_path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return None


def _write_json(file_path: Path, data: Any) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def extract_usage(usage_metadata: dict[str, Any]) -> dict[str, int]:
    """把Gemini回傳的usageMetadata轉成固定欄位。"""
    if not isinstance(usage_metadata, dict):
        usage_metadata = {}

    counts = {
        name: _as_int(usage_metadata.get(api_name))
        for name, api_name in USAGE_FIELDS
    }
    counts["billed_output_tokens"] = (
        counts["output_tokens"] + counts["thinking_tokens"]
    )

    if not counts["total_tokens"]:
        counts["total_tokens"] = (
            counts["input_tokens"] + counts["billed_output_tokens"]
        )

    return counts


def _sum_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    totals = {name: 0 for name in COUNTED_FIELDS}

    for record in records:
        for name in COUNTED_FIELDS:
            totals[name] += _as_int(record.get(name))

    return totals


def _usage_file_for(date_string: str) -> Path:
    year, month = date_string[:4], date_string[5:7]
    return USAGE_DIRECTORY / year / month / f"{date_string}.json"


def record_gemini_usage(
    step: str,
    model: str,
    usage_metadata: dict[str, Any],
    run_at: datetime | None = None,
    note: str = "",
) -> dict[str, int]:
    """把一次Gemini呼叫記進當日帳本，回傳這次呼叫的token統計。"""
    moment = (run_at or datetime.now(TAIPEI_TIMEZONE)).astimezone(
        TAIPEI_TIMEZONE
    )
    date_string = moment.strftime("%Y-%m-%d")
    counts = extract_usage(usage_metadata)

    call_record: dict[str, Any] = {
        "step": step,
        "model": model,
        "called_at": moment.isoformat(),
        "github_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        **counts,
    }

    if note:
        call_record["note"] = note

    usage_file = _usage_file_for(date_string)
    existing = _read_json(usage_file)

    if isinstance(existing, dict) and isinstance(existing.get("calls"), list):
        calls = existing["calls"]
    else:
        calls = []

    calls.append(call_record)

    by_step: dict[str, dict[str, int]] = {}

    for record in calls:
        step_name = str(record.get("step", "unknown"))
        bucket = by_step.setdefault(
            step_name,
            {name: 0 for name in COUNTED_FIELDS} | {"call_count": 0},
        )
        bucket["call_count"] += 1
        for name in COUNTED_FIELDS:
            bucket[name] += _as_int(record.get(name))

    daily = {
        "date": date_string,
        "timezone": "Asia/Taipei",
        "updated_at": moment.isoformat(),
        "call_count": len(calls),
        "totals": _sum_counts(calls),
        "by_step": by_step,
        "calls": calls,
    }

    _write_json(usage_file, daily)
    _write_json(USAGE_LATEST_FILE, daily)
    _update_history(daily)

    return counts


def _update_history(daily: dict[str, Any]) -> None:
    history = _read_json(USAGE_HISTORY_FILE)

    if isinstance(history, dict) and isinstance(history.get("days"), list):
        days = [
            day
            for day in history["days"]
            if isinstance(day, dict) and day.get("date") != daily["date"]
        ]
    else:
        days = []

    days.append(
        {
            "date": daily["date"],
            "call_count": daily["call_count"],
            **daily["totals"],
        }
    )
    days.sort(key=lambda row: str(row.get("date")), reverse=True)
    days = days[:HISTORY_MAX_DAYS]

    _write_json(
        USAGE_HISTORY_FILE,
        {
            "updated_at": daily["updated_at"],
            "timezone": "Asia/Taipei",
            "day_count": len(days),
            "totals_last_30_days": _sum_counts(days[:30]),
            "days": days,
        },
    )


def format_usage_line(step: str, counts: dict[str, int]) -> str:
    """給Actions log看的單行摘要。"""
    return (
        f"[token] {step}: input={counts['input_tokens']:,} "
        f"(cached {counts['cached_input_tokens']:,}) "
        f"output={counts['output_tokens']:,} "
        f"thinking={counts['thinking_tokens']:,} "
        f"billed_output={counts['billed_output_tokens']:,} "
        f"total={counts['total_tokens']:,}"
    )
