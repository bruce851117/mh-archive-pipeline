from __future__ import annotations

import hashlib
import html
import json
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import feedparser
import requests


RSS_URL = "https://www.financialjuice.com/feed.ashx?xy=rss"

TAIPEI_TIMEZONE = timezone(timedelta(hours=8))

DATA_DIRECTORY = Path("data")
ARCHIVE_DIRECTORY = DATA_DIRECTORY / "archive"
LATEST_FILE = DATA_DIRECTORY / "latest_24h.json"
STATUS_FILE = DATA_DIRECTORY / "fetch_status.json"

REQUEST_TIMEOUT_SECONDS = 30

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 "
    "MarketHeadlineArchive/1.0"
)


def utc_now() -> datetime:
    """取得目前UTC時間，用於內部時間計算。"""
    return datetime.now(timezone.utc)


def format_taipei_time(value: datetime) -> str:
    """
    將時間轉換成台灣時間GMT+8。

    範例：
    2026-07-17T14:30:15+08:00
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(TAIPEI_TIMEZONE).isoformat()


def ensure_directories() -> None:
    """建立資料目錄。"""
    DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIRECTORY.mkdir(parents=True, exist_ok=True)


def normalize_text(value: Any) -> str:
    """清理HTML實體、多餘空格與換行。"""
    if value is None:
        return ""

    text = html.unescape(str(value))
    return " ".join(text.split()).strip()


def remove_financialjuice_prefix(title: str) -> str:
    """移除FinancialJuice在標題前方附加的名稱。"""
    prefixes = (
        "FinancialJuice:",
        "Financial Juice:",
    )

    cleaned_title = title.strip()

    for prefix in prefixes:
        if cleaned_title.lower().startswith(prefix.lower()):
            return cleaned_title[len(prefix):].strip()

    return cleaned_title


def normalize_url(value: Any) -> str:
    """檢查並標準化新聞網址。"""
    url = normalize_text(value)

    if not url:
        return ""

    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        return ""

    return url


def parse_published_time(
    entry: Any,
    fallback_time: datetime,
) -> datetime:
    """
    解析RSS發布時間。

    此函式先回傳UTC datetime；
    寫入JSON時再轉成台灣時間GMT+8。
    """
    possible_values = (
        entry.get("published"),
        entry.get("updated"),
        entry.get("created"),
    )

    for value in possible_values:
        if not value:
            continue

        try:
            parsed = parsedate_to_datetime(value)

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            return parsed.astimezone(timezone.utc)

        except (TypeError, ValueError, OverflowError):
            continue

    possible_struct_times = (
        entry.get("published_parsed"),
        entry.get("updated_parsed"),
        entry.get("created_parsed"),
    )

    for struct_time in possible_struct_times:
        if not struct_time:
            continue

        try:
            return datetime(
                struct_time.tm_year,
                struct_time.tm_mon,
                struct_time.tm_mday,
                struct_time.tm_hour,
                struct_time.tm_min,
                struct_time.tm_sec,
                tzinfo=timezone.utc,
            )

        except (AttributeError, TypeError, ValueError):
            continue

    return fallback_time.astimezone(timezone.utc)


def generate_item_id(
    guid: str,
    link: str,
    title: str,
    published_at: str,
) -> str:
    """依GUID、連結或標題與時間產生唯一ID。"""
    if guid:
        raw_id = f"guid:{guid}"
    elif link:
        raw_id = f"link:{link}"
    else:
        raw_id = f"title:{title}|published:{published_at}"

    return hashlib.sha256(raw_id.encode("utf-8")).hexdigest()


def fetch_rss() -> bytes:
    """下載FinancialJuice RSS原始內容。"""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": (
            "application/rss+xml, application/xml, text/xml, "
            "text/html;q=0.9, */*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    response = requests.get(
        RSS_URL,
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    if not response.content:
        raise RuntimeError(
            "FinancialJuice RSS returned an empty response."
        )

    return response.content


def parse_rss(
    rss_content: bytes,
    fetched_at: datetime,
) -> list[dict[str, Any]]:
    """
    解析FinancialJuice RSS。

    輸出的published_at與fetched_at均使用台灣時間GMT+8。
    """
    feed = feedparser.parse(rss_content)

    if feed.bozo and not feed.entries:
        raise RuntimeError(
            "Unable to parse FinancialJuice RSS: "
            f"{feed.bozo_exception}"
        )

    parsed_items: list[dict[str, Any]] = []
    fetched_at_taipei = format_taipei_time(fetched_at)

    for entry in feed.entries:
        raw_title = normalize_text(entry.get("title"))

        if not raw_title:
            continue

        title = remove_financialjuice_prefix(raw_title)
        link = normalize_url(entry.get("link"))
        guid = normalize_text(entry.get("id") or entry.get("guid"))

        published_datetime = parse_published_time(
            entry=entry,
            fallback_time=fetched_at,
        )

        published_datetime_taipei = published_datetime.astimezone(
            TAIPEI_TIMEZONE
        )

        published_at = published_datetime_taipei.isoformat()
        taipei_date = published_datetime_taipei.strftime("%Y-%m-%d")

        categories: list[str] = []

        for tag in entry.get("tags", []):
            term = normalize_text(tag.get("term"))

            if term and term not in categories:
                categories.append(term)

        item_id = generate_item_id(
            guid=guid,
            link=link,
            title=title,
            published_at=published_at,
        )

        parsed_items.append(
            {
                "id": item_id,
                "guid": guid,
                "published_at": published_at,
                "timezone": "Asia/Taipei",
                "utc_offset": "+08:00",
                "taipei_date": taipei_date,
                "headline": title,
                "original_title": raw_title,
                "link": link,
                "categories": categories,
                "source": "FinancialJuice",
                "rss_url": RSS_URL,
                "fetched_at": fetched_at_taipei,
            }
        )

    unique_items: dict[str, dict[str, Any]] = {}

    for item in parsed_items:
        existing_item = unique_items.get(item["id"])

        if existing_item is None:
            unique_items[item["id"]] = item
            continue

        if item["published_at"] > existing_item["published_at"]:
            unique_items[item["id"]] = item

    return sorted(
        unique_items.values(),
        key=lambda item: item["published_at"],
        reverse=True,
    )


def get_archive_file(taipei_date: str) -> Path:
    """依台灣日期取得歷史資料檔案路徑。"""
    try:
        parsed_date = datetime.strptime(taipei_date, "%Y-%m-%d")
    except ValueError as error:
        raise ValueError(
            f"Invalid Taipei date: {taipei_date}"
        ) from error

    year_directory = ARCHIVE_DIRECTORY / parsed_date.strftime("%Y")
    month_directory = year_directory / parsed_date.strftime("%m")

    month_directory.mkdir(parents=True, exist_ok=True)

    return month_directory / f"{taipei_date}.json"


def load_json_list(file_path: Path) -> list[dict[str, Any]]:
    """讀取JSON陣列；檔案不存在時回傳空陣列。"""
    if not file_path.exists():
        return []

    try:
        with file_path.open("r", encoding="utf-8") as file:
            data = json.load(file)

    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError(
            f"Unable to read {file_path}: {error}"
        ) from error

    if not isinstance(data, list):
        raise RuntimeError(
            f"{file_path} must contain a JSON array."
        )

    return data


def write_json(file_path: Path, data: Any) -> None:
    """
    先寫入暫存檔，再取代正式檔案，
    避免寫入中途失敗造成JSON損壞。
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)

    temporary_file = file_path.with_suffix(
        file_path.suffix + ".tmp"
    )

    with temporary_file.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.write("\n")

    temporary_file.replace(file_path)


def merge_items(
    existing_items: list[dict[str, Any]],
    incoming_items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """合併新舊資料並依唯一ID去除重複。"""
    merged_items: dict[str, dict[str, Any]] = {}

    for item in existing_items:
        item_id = normalize_text(item.get("id"))

        if item_id:
            merged_items[item_id] = item

    previous_ids = set(merged_items)

    for item in incoming_items:
        item_id = item["id"]

        if item_id not in merged_items:
            merged_items[item_id] = item

    new_item_count = len(set(merged_items) - previous_ids)

    sorted_items = sorted(
        merged_items.values(),
        key=lambda item: item.get("published_at", ""),
        reverse=True,
    )

    return sorted_items, new_item_count


def save_daily_archives(
    items: list[dict[str, Any]],
) -> tuple[int, list[str]]:
    """依照台灣日期將快訊保存至每日檔案。"""
    items_by_date: dict[str, list[dict[str, Any]]] = {}

    for item in items:
        taipei_date = item["taipei_date"]

        items_by_date.setdefault(
            taipei_date,
            [],
        ).append(item)

    total_new_items = 0
    changed_files: list[str] = []

    for taipei_date, date_items in items_by_date.items():
        archive_file = get_archive_file(taipei_date)
        existing_items = load_json_list(archive_file)

        merged_items, new_item_count = merge_items(
            existing_items=existing_items,
            incoming_items=date_items,
        )

        if new_item_count > 0 or not archive_file.exists():
            write_json(archive_file, merged_items)
            changed_files.append(str(archive_file))

        total_new_items += new_item_count

    return total_new_items, changed_files



def find_archive_files_for_last_24_hours(
    now: datetime,
):
    """
    最近24小時可能跨越兩個台灣日期，
    因此讀取台灣今日與昨日檔案。
    """
    taipei_now = now.astimezone(TAIPEI_TIMEZONE)

    today = taipei_now.date()
    yesterday = today - timedelta(days=1)

    dates = {
        today.strftime("%Y-%m-%d"),
        yesterday.strftime("%Y-%m-%d"),
    }

    return [
        get_archive_file(date_string)
        for date_string in sorted(dates)
    ]


def parse_iso_datetime(value: str) -> datetime | None:
    """解析帶有Z或+08:00時區資訊的ISO時間。"""
    normalized_value = normalize_text(value)

    if not normalized_value:
        return None

    try:
        parsed = datetime.fromisoformat(
            normalized_value.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TAIPEI_TIMEZONE)

    return parsed


def generate_latest_24h(
    now: datetime,
) -> list[dict[str, Any]]:
    """
    產生最近24小時快訊。

    雖然JSON內以GMT+8儲存，
    但比較時計算成UTC，確保24小時區間正確。
    """
    cutoff_time = now - timedelta(hours=24)

    latest_items: dict[str, dict[str, Any]] = {}

    archive_files = find_archive_files_for_last_24_hours(now)

    for archive_file in archive_files:
        for item in load_json_list(archive_file):
            published_datetime = parse_iso_datetime(
                normalize_text(item.get("published_at"))
            )

            if published_datetime is None:
                continue

            published_datetime_utc = published_datetime.astimezone(
                timezone.utc
            )

            if published_datetime_utc < cutoff_time:
                continue

            item_id = normalize_text(item.get("id"))

            if item_id:
                latest_items[item_id] = item

    return sorted(
        latest_items.values(),
        key=lambda item: item.get("published_at", ""),
        reverse=True,
    )


def write_status(
    *,
    status: str,
    fetched_at: datetime,
    rss_item_count: int,
    new_item_count: int,
    latest_24h_count: int,
    changed_files: list[str],
    error_message: str = "",
) -> None:
    """寫入抓取狀態，所有時間使用台灣時間。"""
    fetched_at_taipei = format_taipei_time(fetched_at)

    status_data = {
        "status": status,
        "timezone": "Asia/Taipei",
        "utc_offset": "+08:00",
        "rss_url": RSS_URL,
        "last_attempt_at": fetched_at_taipei,
        "rss_item_count": rss_item_count,
        "new_item_count": new_item_count,
        "latest_24h_count": latest_24h_count,
        "changed_files": changed_files,
        "error": error_message,
    }

    if status == "success":
        status_data["last_success_at"] = fetched_at_taipei

    write_json(STATUS_FILE, status_data)


def main() -> int:
    fetched_at = utc_now()

    ensure_directories()

    try:
        print(f"Fetching RSS: {RSS_URL}")

        rss_content = fetch_rss()

        rss_items = parse_rss(
            rss_content=rss_content,
            fetched_at=fetched_at,
        )

        if not rss_items:
            raise RuntimeError(
                "No valid RSS items were found."
            )

        print(f"Parsed RSS items: {len(rss_items)}")

        new_item_count, changed_files = save_daily_archives(
            rss_items
        )

        latest_24h_items = generate_latest_24h(fetched_at)

        write_json(
            LATEST_FILE,
            latest_24h_items,
        )

        if str(LATEST_FILE) not in changed_files:
            changed_files.append(str(LATEST_FILE))

        write_status(
            status="success",
            fetched_at=fetched_at,
            rss_item_count=len(rss_items),
            new_item_count=new_item_count,
            latest_24h_count=len(latest_24h_items),
            changed_files=changed_files,
        )

        print(f"New items saved: {new_item_count}")
        print(
            "Items in latest 24 hours: "
            f"{len(latest_24h_items)}"
        )
        print(
            "All output timestamps use Asia/Taipei GMT+8."
        )
        print("RSS update completed successfully.")

        return 0

    except Exception as error:
        error_message = f"{type(error).__name__}: {error}"

        print(error_message, file=sys.stderr)

        try:
            write_status(
                status="failed",
                fetched_at=fetched_at,
                rss_item_count=0,
                new_item_count=0,
                latest_24h_count=0,
                changed_files=[],
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