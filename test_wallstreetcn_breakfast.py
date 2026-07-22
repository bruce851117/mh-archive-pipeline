from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

TAIPEI_TZ = timezone(timedelta(hours=8))
DEFAULT_OUTPUT_DIRECTORY = Path("wscn_breakfast_test_output")
SEARCH_PAGE_URL = "https://wallstreetcn.com/search?q=%E6%97%A9%E9%A4%90"
SEARCH_API_BASE = "https://api-one-wscn.awtmt.com/apiv1/search/article"
SEARCH_API_FALLBACK = "https://api-one.wallstcn.com/apiv1/search/article"
ARTICLE_PAGE_BASE = "https://wallstreetcn.com/articles"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def taipei_now() -> datetime:
    return datetime.now(TAIPEI_TZ)


def fetch_url(url: str, timeout: int = 30) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
            "Cache-Control": "no-cache",
        },
    )

    started = time.time()
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            return {
                "ok": True,
                "requested_url": url,
                "final_url": response.geturl(),
                "status": response.status,
                "content_type": response.headers.get("Content-Type", ""),
                "charset": charset,
                "elapsed_seconds": round(time.time() - started, 3),
                "bytes": len(raw),
                "text": text,
                "error": "",
            }
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "requested_url": url,
            "final_url": error.geturl(),
            "status": error.code,
            "content_type": error.headers.get("Content-Type", ""),
            "charset": "utf-8",
            "elapsed_seconds": round(time.time() - started, 3),
            "bytes": len(body.encode("utf-8")),
            "text": body,
            "error": f"HTTPError: {error}",
        }
    except (URLError, TimeoutError, OSError) as error:
        return {
            "ok": False,
            "requested_url": url,
            "final_url": "",
            "status": None,
            "content_type": "",
            "charset": "",
            "elapsed_seconds": round(time.time() - started, 3),
            "bytes": 0,
            "text": "",
            "error": f"{type(error).__name__}: {error}",
        }


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def safe_name(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip())
    return value.strip("_") or "response"


def save_response(directory: Path, name: str, response: dict[str, Any]) -> None:
    metadata = {key: value for key, value in response.items() if key != "text"}
    write_json(directory / f"{name}_meta.json", metadata)
    write_text(directory / f"{name}_body.txt", response.get("text", ""))


def strip_html(value: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6])>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\u3000", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def recursive_find_records(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if any(key in value for key in ("title", "uri", "id", "content_short")):
            records.append(value)
        for child in value.values():
            records.extend(recursive_find_records(child))
    elif isinstance(value, list):
        for child in value:
            records.extend(recursive_find_records(child))
    return records


def normalize_article_records(payload: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()

    for record in recursive_find_records(payload):
        title = str(record.get("title") or "").strip()
        uri = str(record.get("uri") or record.get("url") or "").strip()
        article_id = str(record.get("id") or "").strip()
        content_short = strip_html(str(record.get("content_short") or ""))
        display_time = record.get("display_time")

        if not article_id and uri:
            match = re.search(r"/articles/(\d+)", uri)
            if match:
                article_id = match.group(1)

        dedupe_key = article_id or uri or title
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        output.append(
            {
                "id": article_id,
                "title": title,
                "uri": uri,
                "content_short": content_short,
                "display_time": display_time,
            }
        )

    return output


def expected_title_date(date_text: str) -> str:
    parsed = datetime.strptime(date_text, "%Y-%m-%d")
    return f"{parsed.year}年{parsed.month}月{parsed.day}日"


def choose_breakfast_article(
    records: list[dict[str, Any]],
    target_date: str,
) -> dict[str, Any] | None:
    expected = expected_title_date(target_date)
    candidates = []

    for record in records:
        title = record.get("title", "")
        if "早餐" not in title:
            continue
        score = 0
        if expected in title:
            score += 100
        if "华尔街见闻早餐" in title or "華爾街見聞早餐" in title:
            score += 20
        if "FM-Radio" in title:
            score += 5
        candidates.append((score, record))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, best_record = candidates[0]
    if best_score < 100:
        return None
    return best_record


def extract_json_candidates(page_html: str) -> list[Any]:
    candidates: list[Any] = []
    patterns = [
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    ]
    for pattern in patterns:
        for match in re.findall(pattern, page_html, flags=re.I | re.S):
            try:
                candidates.append(json.loads(html.unescape(match).strip()))
            except json.JSONDecodeError:
                continue
    return candidates


def article_api_candidates(article_id: str) -> list[str]:
    # 華爾街見聞前端API可能調整；測試程式逐一嘗試並完整保存回應。
    return [
        f"https://api-one-wscn.awtmt.com/apiv1/content/articles/{article_id}",
        f"https://api-one.wallstcn.com/apiv1/content/articles/{article_id}",
        f"https://api-one-wscn.awtmt.com/apiv1/content/articles/{article_id}?extract=0",
        f"https://api-one.wallstcn.com/apiv1/content/articles/{article_id}?extract=0",
    ]


def find_long_text_fields(value: Any, path: str = "root") -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if isinstance(child, str) and len(child) >= 100:
                found.append(
                    {
                        "path": child_path,
                        "length": len(child),
                        "preview": strip_html(child)[:500],
                    }
                )
            else:
                found.extend(find_long_text_fields(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(find_long_text_fields(child, f"{path}[{index}]"))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(
        description="測試華爾街見聞早餐搜尋頁、搜尋API、文章頁及文章API回傳內容。"
    )
    parser.add_argument(
        "--date",
        default=taipei_now().strftime("%Y-%m-%d"),
        help="早餐標題日期，格式YYYY-MM-DD；預設為台北今日。",
    )
    parser.add_argument(
        "--article-id",
        default="",
        help="已知文章ID。提供後仍會測試搜尋，但優先使用此ID抓文章。",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_DIRECTORY),
        help="輸出目錄。",
    )
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print("--date格式必須是YYYY-MM-DD", file=sys.stderr)
        return 2

    output_directory = Path(args.output)
    output_directory.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "run_at": taipei_now().isoformat(),
        "target_date": args.date,
        "output_directory": str(output_directory),
        "steps": [],
    }

    print("[1/4] 抓取搜尋頁HTML...")
    search_page = fetch_url(SEARCH_PAGE_URL, args.timeout)
    save_response(output_directory, "01_search_page", search_page)
    write_text(
        output_directory / "01_search_page_visible_text.txt",
        strip_html(search_page.get("text", "")),
    )
    report["steps"].append({
        "name": "search_page",
        **{key: value for key, value in search_page.items() if key != "text"},
    })

    print("[2/4] 抓取搜尋API JSON...")
    query_string = urlencode({"query": "早餐", "limit": 30})
    search_api_responses = []
    records: list[dict[str, Any]] = []

    for index, base_url in enumerate((SEARCH_API_BASE, SEARCH_API_FALLBACK), start=1):
        response = fetch_url(f"{base_url}?{query_string}", args.timeout)
        save_response(output_directory, f"02_search_api_{index}", response)
        search_api_responses.append(response)

        try:
            payload = json.loads(response.get("text", ""))
            write_json(output_directory / f"02_search_api_{index}_parsed.json", payload)
            records.extend(normalize_article_records(payload))
        except json.JSONDecodeError:
            pass

        if response["ok"] and records:
            break

    write_json(output_directory / "02_search_api_normalized_records.json", records)
    chosen = choose_breakfast_article(records, args.date)

    article_id = args.article_id.strip()
    if not article_id and chosen:
        article_id = str(chosen.get("id") or "")

    report["selected_search_record"] = chosen
    report["article_id"] = article_id

    if not article_id:
        print("找不到指定日期的早餐文章ID。")
        print("請查看02_search_api_*與01_search_page_*輸出，或使用--article-id手動指定。")
        write_json(output_directory / "00_report.json", report)
        return 1

    print(f"[3/4] 抓取文章頁HTML，article_id={article_id}...")
    article_page_url = f"{ARTICLE_PAGE_BASE}/{quote(article_id)}?keyword=%E6%97%A9%E9%A4%90"
    article_page = fetch_url(article_page_url, args.timeout)
    save_response(output_directory, "03_article_page", article_page)
    article_visible_text = strip_html(article_page.get("text", ""))
    write_text(output_directory / "03_article_page_visible_text.txt", article_visible_text)

    embedded_json = extract_json_candidates(article_page.get("text", ""))
    write_json(output_directory / "03_article_page_embedded_json.json", embedded_json)
    write_json(
        output_directory / "03_article_page_long_text_fields.json",
        find_long_text_fields(embedded_json),
    )

    print("[4/4] 測試可能的文章API端點...")
    api_results = []
    for index, url in enumerate(article_api_candidates(article_id), start=1):
        response = fetch_url(url, args.timeout)
        save_response(output_directory, f"04_article_api_{index}", response)
        result_summary = {
            "url": url,
            **{key: value for key, value in response.items() if key != "text"},
        }

        try:
            payload = json.loads(response.get("text", ""))
            write_json(output_directory / f"04_article_api_{index}_parsed.json", payload)
            long_fields = find_long_text_fields(payload)
            write_json(
                output_directory / f"04_article_api_{index}_long_text_fields.json",
                long_fields,
            )
            result_summary["json_parsed"] = True
            result_summary["long_text_field_count"] = len(long_fields)
        except json.JSONDecodeError:
            result_summary["json_parsed"] = False
            result_summary["long_text_field_count"] = 0

        api_results.append(result_summary)

    report["article_page"] = {
        **{key: value for key, value in article_page.items() if key != "text"},
        "visible_text_length": len(article_visible_text),
        "embedded_json_count": len(embedded_json),
    }
    report["article_api_results"] = api_results
    write_json(output_directory / "00_report.json", report)

    print("\n測試完成。請依序查看：")
    print(f"1. {output_directory / '00_report.json'}")
    print(f"2. {output_directory / '02_search_api_normalized_records.json'}")
    print(f"3. {output_directory / '03_article_page_visible_text.txt'}")
    print(f"4. {output_directory / '04_article_api_1_parsed.json'}（若存在）")
    print(f"5. {output_directory / '04_article_api_1_long_text_fields.json'}（若存在）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
