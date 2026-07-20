from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


TAIPEI_TIMEZONE = timezone(timedelta(hours=8))

DATA_DIRECTORY = Path("data")
ARCHIVE_DIRECTORY = DATA_DIRECTORY / "archive"
CENTRAL_BANK_CONFIG_FILE = Path("central_bank_officials.json")
CENTRAL_BANK_DIRECTORY = DATA_DIRECTORY / "central_banks"
CENTRAL_BANK_RAW_DIRECTORY = CENTRAL_BANK_DIRECTORY / "raw"
CENTRAL_BANK_LATEST_90D_FILE = (
    CENTRAL_BANK_DIRECTORY / "latest_90d.json"
)
CENTRAL_BANK_STATUS_FILE = (
    CENTRAL_BANK_DIRECTORY / "backfill_status.json"
)
MANUAL_BACKFILL_FILE = Path("manual_central_bank_backfill.json")

LOOKBACK_DAYS = 90


def taipei_now() -> datetime:
    return datetime.now(TAIPEI_TIMEZONE)


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


def format_taipei_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=TAIPEI_TIMEZONE)

    return value.astimezone(TAIPEI_TIMEZONE).isoformat()


def read_json(file_path: Path) -> Any:
    try:
        with file_path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Unable to read {file_path}: {error}"
        ) from error


def read_json_list(
    file_path: Path,
) -> list[dict[str, Any]]:
    if not file_path.exists():
        return []

    data = read_json(file_path)

    if not isinstance(data, list):
        raise RuntimeError(
            f"{file_path} must contain a JSON array."
        )

    return [item for item in data if isinstance(item, dict)]


def write_json(file_path: Path, data: Any) -> None:
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


def load_filter_prefixes() -> dict[str, list[str]]:
    if not CENTRAL_BANK_CONFIG_FILE.exists():
        raise FileNotFoundError(
            "Central bank config does not exist: "
            f"{CENTRAL_BANK_CONFIG_FILE}"
        )

    config = read_json(CENTRAL_BANK_CONFIG_FILE)

    if not isinstance(config, dict):
        raise RuntimeError(
            "central_bank_officials.json must contain "
            "a JSON object."
        )

    raw_prefixes = config.get("filter_prefixes")

    if not isinstance(raw_prefixes, dict):
        raise RuntimeError(
            "central_bank_officials.json is missing "
            "filter_prefixes."
        )

    filter_prefixes: dict[str, list[str]] = {}

    for central_bank, prefixes in raw_prefixes.items():
        bank_code = normalize_text(central_bank).upper()

        if not bank_code or not isinstance(prefixes, list):
            continue

        cleaned_prefixes = [
            normalize_text(prefix)
            for prefix in prefixes
            if normalize_text(prefix)
        ]

        if cleaned_prefixes:
            filter_prefixes[bank_code] = cleaned_prefixes

    if not filter_prefixes:
        raise RuntimeError(
            "No valid central bank filter prefixes were found."
        )

    return filter_prefixes


def identify_central_bank(
    headline: str,
    filter_prefixes: dict[str, list[str]],
) -> str | None:
    """
    只依央行所有格前綴辨識談話。

    casefold讓Fed、FED、FeD等大小寫都能匹配，
    並支援半形撇號及彎曲撇號。
    """
    normalized_headline = normalize_text(headline).casefold()

    if not normalized_headline:
        return None

    for central_bank, prefixes in filter_prefixes.items():
        for prefix in prefixes:
            normalized_prefix = normalize_text(prefix).casefold()

            if (
                normalized_prefix
                and normalized_prefix in normalized_headline
            ):
                return central_bank

    return None


def get_raw_archive_file(taipei_date: str) -> Path:
    try:
        parsed_date = datetime.strptime(
            taipei_date,
            "%Y-%m-%d",
        )
    except ValueError as error:
        raise ValueError(
            f"Invalid Taipei date: {taipei_date}"
        ) from error

    return (
        CENTRAL_BANK_RAW_DIRECTORY
        / parsed_date.strftime("%Y")
        / parsed_date.strftime("%m")
        / f"{taipei_date}.json"
    )


def get_candidate_archive_files(
    period_start: datetime,
    period_end: datetime,
) -> list[Path]:
    files: list[Path] = []
    current_date = period_start.date()
    final_date = period_end.date()

    while current_date <= final_date:
        date_string = current_date.isoformat()
        parsed_date = datetime.strptime(
            date_string,
            "%Y-%m-%d",
        )
        archive_file = (
            ARCHIVE_DIRECTORY
            / parsed_date.strftime("%Y")
            / parsed_date.strftime("%m")
            / f"{date_string}.json"
        )

        if archive_file.exists():
            files.append(archive_file)

        current_date += timedelta(days=1)

    return files


def create_central_bank_item(
    item: dict[str, Any],
    central_bank: str,
) -> dict[str, Any] | None:
    item_id = normalize_text(item.get("id"))
    headline = normalize_text(item.get("headline"))
    published_at = parse_iso_datetime(
        item.get("published_at")
    )

    if not item_id or not headline or published_at is None:
        return None

    taipei_date = normalize_text(item.get("taipei_date"))

    if not taipei_date:
        taipei_date = published_at.strftime("%Y-%m-%d")

    return {
        "id": item_id,
        "published_at": format_taipei_time(published_at),
        "timezone": "Asia/Taipei",
        "utc_offset": "+08:00",
        "taipei_date": taipei_date,
        "central_bank": central_bank,
        "headline": headline,
        "source": normalize_text(item.get("source"))
        or "FinancialJuice",
        "fetched_at": normalize_text(item.get("fetched_at")),
    }


def merge_items(
    existing_items: list[dict[str, Any]],
    incoming_items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    merged: dict[str, dict[str, Any]] = {}

    for item in existing_items:
        item_id = normalize_text(item.get("id"))

        if item_id:
            merged[item_id] = item

    previous_ids = set(merged)

    for item in incoming_items:
        item_id = normalize_text(item.get("id"))

        if item_id and item_id not in merged:
            merged[item_id] = item

    new_item_count = len(set(merged) - previous_ids)

    return (
        sorted(
            merged.values(),
            key=lambda row: row.get("published_at", ""),
            reverse=True,
        ),
        new_item_count,
    )


def count_by_bank(
    items: list[dict[str, Any]],
    bank_codes: list[str],
) -> dict[str, int]:
    counts = {bank_code: 0 for bank_code in bank_codes}

    for item in items:
        central_bank = normalize_text(
            item.get("central_bank")
        ).upper()

        if central_bank in counts:
            counts[central_bank] += 1

    return counts


def main() -> int:
    run_at = taipei_now()
    period_end = run_at
    period_start = run_at - timedelta(days=LOOKBACK_DAYS)

    CENTRAL_BANK_RAW_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        filter_prefixes = load_filter_prefixes()
        bank_codes = list(filter_prefixes)
        archive_files = get_candidate_archive_files(
            period_start,
            period_end,
        )

        candidates_scanned = 0
        manual_candidates_scanned = 0
        matched_by_id: dict[str, dict[str, Any]] = {}
        matched_by_date: dict[str, list[dict[str, Any]]] = {}

        # Read curated manual history first. These rows already contain a
        # central_bank field and are merged with the ordinary RSS archive by ID.
        if MANUAL_BACKFILL_FILE.exists():
            for item in read_json_list(MANUAL_BACKFILL_FILE):
                manual_candidates_scanned += 1
                published_at = parse_iso_datetime(item.get("published_at"))
                if published_at is None or not (period_start <= published_at <= period_end):
                    continue

                central_bank = normalize_text(item.get("central_bank")).upper()
                if central_bank not in bank_codes:
                    headline = normalize_text(item.get("headline"))
                    central_bank = identify_central_bank(headline, filter_prefixes) or ""
                if central_bank not in bank_codes:
                    continue

                selected = create_central_bank_item(item, central_bank)
                if selected is not None:
                    matched_by_id[selected["id"]] = selected

        for archive_file in archive_files:
            for item in read_json_list(archive_file):
                candidates_scanned += 1
                published_at = parse_iso_datetime(
                    item.get("published_at")
                )

                if published_at is None:
                    continue

                if not (period_start <= published_at <= period_end):
                    continue

                headline = normalize_text(item.get("headline"))
                central_bank = identify_central_bank(
                    headline,
                    filter_prefixes,
                )

                if central_bank is None:
                    continue

                selected = create_central_bank_item(
                    item,
                    central_bank,
                )

                if selected is None:
                    continue

                matched_by_id[selected["id"]] = selected

        matched_items = sorted(
            matched_by_id.values(),
            key=lambda row: row.get("published_at", ""),
            reverse=True,
        )

        for item in matched_items:
            matched_by_date.setdefault(
                item["taipei_date"],
                [],
            ).append(item)

        changed_files: list[str] = []
        total_new_items = 0

        for taipei_date, date_items in matched_by_date.items():
            raw_file = get_raw_archive_file(taipei_date)
            existing_items = read_json_list(raw_file)
            merged_items, new_item_count = merge_items(
                existing_items,
                date_items,
            )

            if new_item_count > 0 or not raw_file.exists():
                write_json(raw_file, merged_items)
                changed_files.append(str(raw_file))

            total_new_items += new_item_count

        all_recent_items: dict[str, dict[str, Any]] = {}

        for raw_file in CENTRAL_BANK_RAW_DIRECTORY.glob(
            "*/*/*.json"
        ):
            for item in read_json_list(raw_file):
                published_at = parse_iso_datetime(
                    item.get("published_at")
                )

                if published_at is None:
                    continue

                if not (period_start <= published_at <= period_end):
                    continue

                item_id = normalize_text(item.get("id"))

                if item_id:
                    all_recent_items[item_id] = item

        latest_90d_items = sorted(
            all_recent_items.values(),
            key=lambda row: row.get("published_at", ""),
            reverse=True,
        )

        write_json(
            CENTRAL_BANK_LATEST_90D_FILE,
            latest_90d_items,
        )
        changed_files.append(
            str(CENTRAL_BANK_LATEST_90D_FILE)
        )

        status_data = {
            "status": "success",
            "timezone": "Asia/Taipei",
            "utc_offset": "+08:00",
            "run_at": format_taipei_time(run_at),
            "period_start": format_taipei_time(period_start),
            "period_end": format_taipei_time(period_end),
            "lookback_days": LOOKBACK_DAYS,
            "archive_file_count": len(archive_files),
            "candidate_headline_count": candidates_scanned,
            "manual_candidate_count": manual_candidates_scanned,
            "matched_headline_count": len(matched_items),
            "matched_by_bank": count_by_bank(
                matched_items,
                bank_codes,
            ),
            "new_item_count": total_new_items,
            "latest_90d_count": len(latest_90d_items),
            "latest_90d_by_bank": count_by_bank(
                latest_90d_items,
                bank_codes,
            ),
            "changed_files": changed_files,
            "error": "",
        }
        write_json(CENTRAL_BANK_STATUS_FILE, status_data)

        print(
            f"Archive files scanned: {len(archive_files)}"
        )
        print(
            f"Candidate headlines scanned: {candidates_scanned}"
        )
        print(
            f"Manual candidates scanned: {manual_candidates_scanned}"
        )
        print(
            f"Central bank headlines matched: {len(matched_items)}"
        )
        print(
            f"Matched by bank: "
            f"{count_by_bank(matched_items, bank_codes)}"
        )
        print(f"New items written: {total_new_items}")
        print(
            f"Latest 90-day items: {len(latest_90d_items)}"
        )
        print(
            f"Output: {CENTRAL_BANK_LATEST_90D_FILE}"
        )
        print(f"Status: {CENTRAL_BANK_STATUS_FILE}")

        return 0

    except Exception as error:
        error_message = f"{type(error).__name__}: {error}"
        print(error_message)

        try:
            write_json(
                CENTRAL_BANK_STATUS_FILE,
                {
                    "status": "failed",
                    "timezone": "Asia/Taipei",
                    "utc_offset": "+08:00",
                    "run_at": format_taipei_time(run_at),
                    "lookback_days": LOOKBACK_DAYS,
                    "error": error_message,
                },
            )
        except Exception as status_error:
            print(
                "Unable to write failure status: "
                f"{status_error}"
            )

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
