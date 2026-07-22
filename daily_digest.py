from __future__ import annotations

import json
import os
import re
import sys
import time
from html.parser import HTMLParser
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
DIGEST_DIRECTORY = Path("data/digests")
HISTORY_INDEX_FILE = DIGEST_DIRECTORY / "history_index.json"
CENTRAL_BANK_CONFIG_FILE = Path("central_bank_officials.json")
CENTRAL_BANK_INPUT_FILE = Path("data/central_banks/latest_90d.json")
CENTRAL_BANK_DIGEST_DIRECTORY = Path("data/central_banks/digests")
CENTRAL_BANK_LATEST_DIGEST_FILE = (
    CENTRAL_BANK_DIGEST_DIRECTORY / "latest.json"
)
CENTRAL_BANK_STATUS_FILE = Path(
    "data/central_banks/digest_status.json"
)
CENTRAL_BANK_LOOKBACK_DAYS = 90

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"

REQUEST_TIMEOUT_SECONDS = 300
MAX_RETRIES = 3
MAX_OUTPUT_TOKENS = 65536
MAX_INPUT_CHARACTERS = 500_000

WALLSTREETCN_SEARCH_URLS = [
    "https://api-one-wscn.awtmt.com/apiv1/search/article",
    "https://api-one.wallstcn.com/apiv1/search/article",
]
WALLSTREETCN_ARTICLE_URLS = [
    "https://api-one-wscn.awtmt.com/apiv1/content/articles/{article_id}?extract=0",
    "https://api-one.wallstcn.com/apiv1/content/articles/{article_id}?extract=0",
]
WALLSTREETCN_TIMEOUT_SECONDS = 30
WALLSTREETCN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
}

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

請盡量都把Headline留下來!!!!!!!!!!，不過以下我所提到的這些幫我刪除：
1.市場估計中國人民銀行可能將人民幣兌美元中間價設定在6.7734元。-->不會對市場造成影響
2.日本財務大臣片山皋月表示，不會評論特定匯率水準。-->除非他提到要干預，不然也不會對市場造成影響
3.台股跌幅超過2.7%。-->純講價格/漲跌幅 不重要
4.川普開始發表全國演說。-->沒有演說內容 不重要
5.白宮表示，情報顯示中國為這項新計畫專門成立一個資料利用單位。-->對市場沒有意義
6.中國商務部表示，將密切關注British Steel事件發展，並支持中國企業依法維護權益。-->對市場沒有意義
7.中國國家主席習近平表示，中國將人工智慧發展安全列為優先事項。-->中國官方說的話，除非提到關稅、貨幣政策及財政支出，都不會對市場造成影響
8.30日相關係數矩陣。、標普500主要成分股隱含波動率。、外匯隱含波動率。-->沒意義
9.亞塞拜然能源部表示，1至6月石油出口量為1,050萬噸。-->這種純報數據的 尤其是小國家 不需要
10.中國外匯管理局表示，上半年外資流入中國轉為淨流入，對外投資維持穩定成長。-->如果是中國的官方機構講的話，如果不是貨幣政策或關稅或財政政策或貿易政策，尤其是那種強烈反對or督促之類的，都刪掉
11.RBA Interest Rate Probabilities-->這個完全沒意義 又沒有數字
12.歐洲議會外交事務委員會將於 7 月 21 日至 23 日訪問中國-->這種誰訪問誰，除非跟最近時事有關，以最近為例，就是美伊在打仗，然後美國要訪問哪個國家進行談判，否則都不重要

這類Headline我覺得很重要，請幫我留下來：
1.川普（Trump）：今晚我們再次重擊伊朗-->這種戰爭有可能升級的言論，非常重要
2.伊朗或是美國有提到希望會談或是不想會談 -->這種戰爭有可能降溫的新聞，非常重要，你已經刪除很多次這類新聞了，請不要再犯，這非常重要!!
3.Google 新晶片將提升 AI 模型運行效率——The Information-->這種大型科技公司的新聞，也請幫我保留
4.Burnham：不會拿經濟冒險，承諾遵守財政規則 / Burnham：調整個人所得稅免稅額將帶來重大影響-->這種已開發經濟體，財政政策非常重要，請幫我保留
5.如果有提到美國要對哪個國家新增關稅，這個也很重要


請對每一則留下的新聞評估重要性1至5分，並將分數填入 importance_score：

5分：可能立即且顯著影響全球主要股市、債市、外匯、商品、能源供應或主要央行政策預期。
4分：可能顯著影響主要國家、市場、資產類別、大型企業或重要政策。
3分：具有明確市場參考價值，但影響較局部，或市場影響仍待觀察。
2分：市場影響有限，但可作為重要事件的背景資訊。
1分：市場影響很小，但仍具有少量資訊價值。

被刪除的Headline不需要評分。
""".strip()


CENTRAL_BANK_SYSTEM_INSTRUCTION = """
你是專業的全球央行政策研究員。你會收到最近90天FinancialJuice的Fed、BoE、ECB、BoJ及RBA官員英文Headline，以及官員正式姓名清單。

請嚴格遵守：
1. 五個央行必須放在同一次整理中完成。
2. 依central_bank、official、date分組；同一官員同一天的多則Headline合併成一句繁體中文摘要。
3. summary_zh只摘要官員對經濟、通膨、勞動市場、貨幣政策、利率、資產負債表、QE或QT的實質看法。
4. 金融監管、銀行資本規範、支付系統、加密貨幣、行政事項、行程、開始演說、結束演說及無實質政策內容的Headline全部忽略。
5. 官員沒有評論的主題必須留空，不可猜測或補充Headline未提供的內容。
6. 每位官員每日summary_zh只能是一句話，但必須完整保留當日不同Headline間的政策訊息。
7. official必須使用官員清單提供的display_name；無法對應官員時不輸出該筆。
8. date必須使用Headline的台灣日期，格式YYYY-MM-DD。
9. source_ids只能使用輸入資料提供的id。
10. 使用台灣繁體中文；只輸出合法JSON，不要Markdown或額外說明。
11. 每筆只能輸出central_bank、official、date、summary_zh、source_ids五個欄位。
12. 不要輸出topics、source_headlines或任何其他欄位。
13. summary_zh控制在100個中文字以內。
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


class WallstreetCNHeadlineParser(HTMLParser):
    """只解析早餐文章中「要闻」标题后的第一个blockquote。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found_headline_heading = False
        self.in_h2 = False
        self.h2_parts: list[str] = []
        self.in_target_blockquote = False
        self.blockquote_depth = 0
        self.capture_tag = ""
        self.capture_depth = 0
        self.capture_parts: list[str] = []
        self.strong_depth = 0
        self.strong_parts: list[str] = []
        self.sections: dict[str, list[str]] = {}
        self.current_section = ""

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        tag = tag.lower()

        if tag == "h2" and not self.found_headline_heading:
            self.in_h2 = True
            self.h2_parts = []
            return

        if (
            tag == "blockquote"
            and self.found_headline_heading
            and not self.in_target_blockquote
        ):
            self.in_target_blockquote = True
            self.blockquote_depth = 1
            return

        if not self.in_target_blockquote:
            return

        if tag == "blockquote":
            self.blockquote_depth += 1

        if not self.capture_tag and tag in {"p", "li"}:
            self.capture_tag = tag
            self.capture_depth = 1
            self.capture_parts = []
            self.strong_depth = 0
            self.strong_parts = []
            return

        if self.capture_tag:
            if tag == self.capture_tag:
                self.capture_depth += 1
            if tag == "strong":
                self.strong_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if tag == "h2" and self.in_h2:
            heading = normalize_text("".join(self.h2_parts))
            self.in_h2 = False
            if heading in {"要闻", "要聞"}:
                self.found_headline_heading = True
            return

        if not self.in_target_blockquote:
            return

        if self.capture_tag:
            if tag == "strong" and self.strong_depth:
                self.strong_depth -= 1
            if tag == self.capture_tag:
                self.capture_depth -= 1
                if self.capture_depth == 0:
                    self._finish_item()

        if tag == "blockquote":
            self.blockquote_depth -= 1
            if self.blockquote_depth <= 0:
                self.in_target_blockquote = False

    def handle_data(self, data: str) -> None:
        if self.in_h2:
            self.h2_parts.append(data)

        if not self.in_target_blockquote or not self.capture_tag:
            return

        self.capture_parts.append(data)
        if self.strong_depth:
            self.strong_parts.append(data)

    def _finish_item(self) -> None:
        text = normalize_text("".join(self.capture_parts))
        strong_text = normalize_text("".join(self.strong_parts))

        # 分类标题必须是独立的粗体短行，避免把事件内粗体误判为分类。
        is_section_heading = (
            bool(text)
            and text == strong_text
            and len(text) <= 30
            and not re.search(r"[。！？!?：:；;]", text)
        )

        if is_section_heading:
            self.current_section = text
            self.sections.setdefault(text, [])
        elif text:
            section = self.current_section or "未分類"
            self.sections.setdefault(section, []).append(text)

        self.capture_tag = ""
        self.capture_depth = 0
        self.capture_parts = []
        self.strong_depth = 0
        self.strong_parts = []


def strip_html_tags(value: Any) -> str:
    text = re.sub(r"(?s)<[^>]+>", " ", str(value or ""))
    return normalize_text(text)


def iter_wallstreetcn_records(value: Any):
    if isinstance(value, dict):
        if "title" in value and ("id" in value or "uri" in value):
            yield value
        for child in value.values():
            yield from iter_wallstreetcn_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_wallstreetcn_records(child)


def wallstreetcn_title_date(run_at: datetime) -> str:
    local_date = run_at.astimezone(TAIPEI_TIMEZONE)
    return f"{local_date.year}年{local_date.month}月{local_date.day}日"


def find_today_wallstreetcn_breakfast(
    run_at: datetime,
) -> dict[str, Any] | None:
    expected_date = wallstreetcn_title_date(run_at)

    for search_url in WALLSTREETCN_SEARCH_URLS:
        try:
            response = requests.get(
                search_url,
                params={"query": "早餐", "limit": 30},
                headers=WALLSTREETCN_HEADERS,
                timeout=WALLSTREETCN_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
        except (
            requests.RequestException,
            ValueError,
            TypeError,
        ) as error:
            print(
                "Warning: WallstreetCN breakfast search failed: "
                f"{type(error).__name__}: {error}",
                file=sys.stderr,
            )
            continue

        for record in iter_wallstreetcn_records(payload):
            title = strip_html_tags(record.get("title"))
            if "早餐" not in title or expected_date not in title:
                continue

            article_id = normalize_text(record.get("id"))
            if not article_id:
                uri = normalize_text(record.get("uri"))
                match = re.search(r"/articles/(\d+)", uri)
                article_id = match.group(1) if match else ""

            if article_id:
                return {
                    "article_id": article_id,
                    "title": title,
                    "source_url": normalize_text(record.get("uri")),
                }

    return None


def fetch_wallstreetcn_breakfast(
    run_at: datetime,
) -> dict[str, Any] | None:
    """尽力抓取当日早餐；任何异常都回传None，不影响原Digest。"""
    article = find_today_wallstreetcn_breakfast(run_at)
    if not article:
        print(
            "WallstreetCN breakfast: no breakfast found for "
            f"{run_at.astimezone(TAIPEI_TIMEZONE).date()}; skipped."
        )
        return None

    article_id = article["article_id"]
    content = ""

    for url_template in WALLSTREETCN_ARTICLE_URLS:
        try:
            response = requests.get(
                url_template.format(article_id=article_id),
                headers=WALLSTREETCN_HEADERS,
                timeout=WALLSTREETCN_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data", {})
            if isinstance(data, dict):
                content = str(data.get("content") or "")
            if content:
                break
        except (
            requests.RequestException,
            ValueError,
            TypeError,
        ) as error:
            print(
                "Warning: WallstreetCN breakfast article fetch failed: "
                f"{type(error).__name__}: {error}",
                file=sys.stderr,
            )

    if not content:
        print(
            "WallstreetCN breakfast: article content unavailable; skipped.",
            file=sys.stderr,
        )
        return None

    parser = WallstreetCNHeadlineParser()
    try:
        parser.feed(content)
        parser.close()
    except Exception as error:
        print(
            "Warning: WallstreetCN breakfast parsing failed: "
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return None

    sections = {
        section: events
        for section, events in parser.sections.items()
        if events
    }
    event_count = sum(len(events) for events in sections.values())

    if not parser.found_headline_heading or event_count == 0:
        print(
            "WallstreetCN breakfast: headline section unavailable or empty; "
            "skipped."
        )
        return None

    result = {
        **article,
        "sections": sections,
        "event_count": event_count,
    }
    print(
        "WallstreetCN breakfast: "
        f"{article['title']} | {len(sections)} sections | "
        f"{event_count} must-keep events"
    )
    return result


def build_wallstreetcn_breakfast_prompt(
    breakfast: dict[str, Any] | None,
) -> str:
    if not breakfast or not breakfast.get("event_count"):
        return ""

    section_lines: list[str] = []
    sections = breakfast.get("sections", {})
    if not isinstance(sections, dict):
        return ""

    for section, events in sections.items():
        if not isinstance(events, list) or not events:
            continue
        section_lines.append(f"{normalize_text(section)}：")
        section_lines.extend(
            f"- {normalize_text(event)}"
            for event in events
            if normalize_text(event)
        )

    if not section_lines:
        return ""

    return """
【今日华尔街见闻早餐－最高优先必保留事件】

以下内容来自今日早餐的「要闻」区块，仅用于判断输入的FinancialJuice Headline是否必须保留，不是额外新闻来源。
若FinancialJuice Headline与以下任一事件属于同一事件、相同公司发展、相同政策、相同经济数据，或是该事件的直接后续进展，必须保留。本规则优先于其他删除规则。

必须遵守：
1. 不得直接把早餐文字新增为新闻。
2. 最终输出的source_id只能使用FinancialJuice输入资料提供的id。
3. 不得根据早餐补充FinancialJuice输入中没有的事实。
4. 不要因为早餐提到广泛主题，就保留所有同类Headline；必须与具体事件具有明确关联。

今日早餐必保留事件：
""".strip() + "\n" + "\n".join(section_lines)


def build_user_prompt(
    compact_input: str,
    input_count: int,
    included_count: int,
    period_start: str,
    period_end: str,
    breakfast_prompt: str = "",
) -> str:
    prompt = f"""
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

    if not breakfast_prompt:
        return prompt

    insertion_point = "\n\n請完成分類、語意去重、事件軌跡、繁體中文翻譯及重要性1至5分評估。"
    if insertion_point not in prompt:
        return breakfast_prompt + "\n\n" + prompt

    return prompt.replace(
        insertion_point,
        "\n\n" + breakfast_prompt + insertion_point,
        1,
    )


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
    system_instruction: str = SYSTEM_INSTRUCTION,
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
            "parts": [{"text": system_instruction}]
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



def read_json_object(file_path: Path) -> dict[str, Any]:
    if not file_path.exists():
        raise FileNotFoundError(
            f"Input file does not exist: {file_path}"
        )

    try:
        with file_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Unable to read {file_path}: {error}"
        ) from error

    if not isinstance(data, dict):
        raise RuntimeError(
            f"{file_path} must contain a JSON object."
        )

    return data


def build_central_bank_reference(
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    bank_blocks: list[dict[str, Any]] = []
    official_lookup: dict[str, dict[str, Any]] = {}
    raw_banks = config.get("central_banks", {})

    if not isinstance(raw_banks, dict):
        raise RuntimeError(
            "central_bank_officials.json is missing central_banks."
        )

    for bank_code, bank_config in raw_banks.items():
        if not isinstance(bank_config, dict):
            continue

        normalized_bank = normalize_text(bank_code).upper()
        display_name = normalize_text(
            bank_config.get("display_name")
        ) or normalized_bank
        officials = bank_config.get("officials", [])
        reference_officials: list[dict[str, Any]] = []

        if not isinstance(officials, list):
            officials = []

        for official in officials:
            if not isinstance(official, dict):
                continue

            display = normalize_text(official.get("display_name"))
            headline_name = normalize_text(
                official.get("headline_name")
            )

            if not display or not headline_name:
                continue

            try:
                priority = int(official.get("priority", 999))
            except (TypeError, ValueError):
                priority = 999

            normalized = {
                "official": display,
                "headline_name": headline_name,
                "priority": priority,
                "position": normalize_text(official.get("position")),
                "active": bool(official.get("active", True)),
            }
            reference_officials.append(normalized)
            official_lookup[f"{normalized_bank}|{display.casefold()}"] = normalized

        reference_officials.sort(key=lambda row: row["priority"])
        bank_blocks.append(
            {
                "central_bank": normalized_bank,
                "display_name": display_name,
                "officials": reference_officials,
            }
        )

    return bank_blocks, official_lookup


def clean_central_bank_input(
    items: list[dict[str, Any]],
    run_at: datetime,
) -> list[dict[str, str]]:
    cutoff = run_at - timedelta(days=CENTRAL_BANK_LOOKBACK_DAYS)
    cleaned_by_id: dict[str, dict[str, str]] = {}

    for item in items:
        item_id = normalize_text(item.get("id"))
        headline = normalize_text(item.get("headline"))
        central_bank = normalize_text(
            item.get("central_bank")
        ).upper()
        published_at = parse_iso_datetime(item.get("published_at"))

        if (
            not item_id
            or not headline
            or not central_bank
            or published_at is None
            or published_at < cutoff
            or published_at > run_at
        ):
            continue

        cleaned_by_id[item_id] = {
            "id": item_id,
            "time": published_at.isoformat(),
            "central_bank": central_bank,
            "headline": headline,
        }

    return sorted(
        cleaned_by_id.values(),
        key=lambda row: row["time"],
    )


def build_central_bank_prompt(
    items: list[dict[str, str]],
    bank_reference: list[dict[str, Any]],
    period_start: str,
    period_end: str,
) -> str:
    compact_reference = [
        {
            "central_bank": bank["central_bank"],
            "officials": [
                {
                    "headline_name": official["headline_name"],
                    "display_name": official["official"],
                }
                for official in bank["officials"]
            ],
        }
        for bank in bank_reference
    ]

    return f"""
請整理以下五大央行最近90天的官員Headline。
統計期間：{period_start} 至 {period_end}
時區：Asia/Taipei（GMT+8）
輸入Headline數：{len(items)}

官員正式姓名清單：
{json.dumps(compact_reference, ensure_ascii=False, separators=(",", ":"))}

輸出必須符合以下JSON結構：
{{
  "talks":[
    {{
      "central_bank":"FED",
      "official":"Christopher Waller",
      "date":"2026-07-17",
      "summary_zh":"同一官員當日相關談話合併後的一句繁體中文摘要",
      "source_ids":["輸入資料中的ID"]
    }}
  ]
}}

輸入資料：
{json.dumps(items, ensure_ascii=False, separators=(",", ":"))}
""".strip()


def normalize_central_bank_digest(
    raw_digest: dict[str, Any],
    bank_reference: list[dict[str, Any]],
    official_lookup: dict[str, dict[str, Any]],
    source_lookup: dict[str, dict[str, str]],
    run_at: datetime,
    model: str,
    usage: dict[str, Any],
) -> dict[str, Any]:
    talks_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    raw_talks = raw_digest.get("talks", [])

    if isinstance(raw_talks, list):
        for talk in raw_talks:
            if not isinstance(talk, dict):
                continue

            central_bank = normalize_text(
                talk.get("central_bank")
            ).upper()
            official_name = normalize_text(talk.get("official"))
            talk_date = normalize_text(talk.get("date"))
            summary = normalize_text(talk.get("summary_zh"))
            official_config = official_lookup.get(
                f"{central_bank}|{official_name.casefold()}"
            )

            if (
                official_config is None
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", talk_date)
                or not summary
            ):
                continue

            valid_source_ids: list[str] = []
            raw_source_ids = talk.get("source_ids", [])

            if isinstance(raw_source_ids, list):
                for source_id in raw_source_ids:
                    normalized_id = normalize_text(source_id)
                    source = source_lookup.get(normalized_id)

                    if (
                        normalized_id
                        and source is not None
                        and source["central_bank"] == central_bank
                        and normalized_id not in valid_source_ids
                    ):
                        valid_source_ids.append(normalized_id)

            if not valid_source_ids:
                continue

            raw_topics = talk.get("topics", {})

            if not isinstance(raw_topics, dict):
                raw_topics = {}

            topics = {
                "economy": normalize_text(raw_topics.get("economy")),
                "inflation": normalize_text(raw_topics.get("inflation")),
                "labor_market": normalize_text(
                    raw_topics.get("labor_market")
                ),
                "monetary_policy": normalize_text(
                    raw_topics.get("monetary_policy")
                ),
                "interest_rates": normalize_text(
                    raw_topics.get("interest_rates")
                ),
                "balance_sheet": normalize_text(
                    raw_topics.get("balance_sheet")
                ),
            }

            key = (central_bank, official_name, talk_date)
            talks_by_key[key] = {
                "date": talk_date,
                "summary_zh": summary,
                "topics": topics,
                "source_ids": valid_source_ids,
                "source_headlines": [
                    source_lookup[source_id]
                    for source_id in valid_source_ids
                ],
            }

    output_banks: list[dict[str, Any]] = []

    for bank in bank_reference:
        bank_code = bank["central_bank"]
        output_officials: list[dict[str, Any]] = []

        for official in bank["officials"]:
            official_talks = [
                talk
                for (code, name, _), talk in talks_by_key.items()
                if code == bank_code and name == official["official"]
            ]
            official_talks.sort(
                key=lambda row: row["date"],
                reverse=True,
            )
            output_officials.append(
                {
                    "official": official["official"],
                    "headline_name": official["headline_name"],
                    "priority": official["priority"],
                    "position": official["position"],
                    "active": official["active"],
                    "talks": official_talks,
                }
            )

        output_banks.append(
            {
                "central_bank": bank_code,
                "display_name": bank["display_name"],
                "officials": output_officials,
            }
        )

    return {
        "generated_at": format_taipei_time(run_at),
        "timezone": "Asia/Taipei",
        "period_start": format_taipei_time(
            run_at - timedelta(days=CENTRAL_BANK_LOOKBACK_DAYS)
        ),
        "period_end": format_taipei_time(run_at),
        "lookback_days": CENTRAL_BANK_LOOKBACK_DAYS,
        "model": model,
        "input_headline_count": len(source_lookup),
        "talk_count": len(talks_by_key),
        "central_banks": output_banks,
        "usage_metadata": usage,
    }


def generate_central_bank_digest(
    api_key: str,
    model: str,
    run_at: datetime,
) -> dict[str, Any]:
    config = read_json_object(CENTRAL_BANK_CONFIG_FILE)
    bank_reference, official_lookup = build_central_bank_reference(
        config
    )
    raw_items = read_json_list(CENTRAL_BANK_INPUT_FILE)
    items = clean_central_bank_input(raw_items, run_at)
    source_lookup = {item["id"]: item for item in items}

    if not items:
        empty_digest = normalize_central_bank_digest(
            raw_digest={"talks": []},
            bank_reference=bank_reference,
            official_lookup=official_lookup,
            source_lookup=source_lookup,
            run_at=run_at,
            model=model,
            usage={},
        )
        return empty_digest

    prompt = build_central_bank_prompt(
        items=items,
        bank_reference=bank_reference,
        period_start=format_taipei_time(
            run_at - timedelta(days=CENTRAL_BANK_LOOKBACK_DAYS)
        ),
        period_end=format_taipei_time(run_at),
    )

    print("")
    print("Generating five-central-bank 90-day digest...")
    print(f"Central bank headlines sent to Gemini: {len(items)}")

    raw_digest, usage = call_gemini(
        api_key=api_key,
        model=model,
        prompt=prompt,
        system_instruction=CENTRAL_BANK_SYSTEM_INSTRUCTION,
    )

    return normalize_central_bank_digest(
        raw_digest=raw_digest,
        bank_reference=bank_reference,
        official_lookup=official_lookup,
        source_lookup=source_lookup,
        run_at=run_at,
        model=model,
        usage=usage,
    )


def load_optional_json_object(
    file_path: Path,
) -> dict[str, Any]:
    """讀取可選JSON物件；檔案不存在時回傳空物件。"""
    if not file_path.exists():
        return {}

    try:
        with file_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Unable to read {file_path}: {error}"
        ) from error

    if not isinstance(data, dict):
        raise RuntimeError(
            f"{file_path} must contain a JSON object."
        )

    return data


def build_central_bank_daily_archives(
    digest: dict[str, Any],
) -> list[Path]:
    """
    將完整近90天央行Digest拆成「每個談話日期一個精簡檔」。

    完整近90天資料仍只寫入digests/latest.json供網頁使用；
    每日歷史檔只保存該日期實際有談話的官員，不保存空白官員，
    也不再複製完整90天資料。
    """
    talks_by_date: dict[str, list[dict[str, Any]]] = {}
    central_banks = digest.get("central_banks", [])

    if not isinstance(central_banks, list):
        return []

    for bank in central_banks:
        if not isinstance(bank, dict):
            continue

        central_bank = normalize_text(
            bank.get("central_bank")
        ).upper()
        bank_display_name = normalize_text(
            bank.get("display_name")
        ) or central_bank
        officials = bank.get("officials", [])

        if not central_bank or not isinstance(officials, list):
            continue

        for official in officials:
            if not isinstance(official, dict):
                continue

            official_name = normalize_text(
                official.get("official")
            )
            headline_name = normalize_text(
                official.get("headline_name")
            )

            if not official_name:
                continue

            try:
                priority = int(official.get("priority", 999))
            except (TypeError, ValueError):
                priority = 999

            talks = official.get("talks", [])

            if not isinstance(talks, list):
                continue

            for talk in talks:
                if not isinstance(talk, dict):
                    continue

                talk_date = normalize_text(talk.get("date"))

                if not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}",
                    talk_date,
                ):
                    continue

                summary_zh = normalize_text(
                    talk.get("summary_zh")
                )

                if not summary_zh:
                    continue

                source_ids = talk.get("source_ids", [])
                source_headlines = talk.get(
                    "source_headlines",
                    [],
                )
                topics = talk.get("topics", {})

                if not isinstance(source_ids, list):
                    source_ids = []

                if not isinstance(source_headlines, list):
                    source_headlines = []

                if not isinstance(topics, dict):
                    topics = {}

                talks_by_date.setdefault(
                    talk_date,
                    [],
                ).append(
                    {
                        "central_bank": central_bank,
                        "central_bank_display_name": (
                            bank_display_name
                        ),
                        "official": official_name,
                        "headline_name": headline_name,
                        "priority": priority,
                        "position": normalize_text(
                            official.get("position")
                        ),
                        "active": bool(
                            official.get("active", True)
                        ),
                        "date": talk_date,
                        "summary_zh": summary_zh,
                        "topics": {
                            "economy": normalize_text(
                                topics.get("economy")
                            ),
                            "inflation": normalize_text(
                                topics.get("inflation")
                            ),
                            "labor_market": normalize_text(
                                topics.get("labor_market")
                            ),
                            "monetary_policy": normalize_text(
                                topics.get("monetary_policy")
                            ),
                            "interest_rates": normalize_text(
                                topics.get("interest_rates")
                            ),
                            "balance_sheet": normalize_text(
                                topics.get("balance_sheet")
                            ),
                        },
                        "source_ids": [
                            normalize_text(source_id)
                            for source_id in source_ids
                            if normalize_text(source_id)
                        ],
                        "source_headlines": [
                            source
                            for source in source_headlines
                            if isinstance(source, dict)
                        ],
                    }
                )

    written_files: list[Path] = []

    for talk_date, talks in sorted(talks_by_date.items()):
        parsed_date = datetime.strptime(
            talk_date,
            "%Y-%m-%d",
        )
        daily_file = (
            CENTRAL_BANK_DIGEST_DIRECTORY
            / parsed_date.strftime("%Y")
            / parsed_date.strftime("%m")
            / f"{talk_date}.json"
        )

        unique_talks: dict[
            tuple[str, str, str],
            dict[str, Any],
        ] = {}

        for talk in talks:
            unique_key = (
                talk["central_bank"],
                talk["official"],
                talk["date"],
            )
            unique_talks[unique_key] = talk

        sorted_talks = sorted(
            unique_talks.values(),
            key=lambda row: (
                row["central_bank"],
                row["priority"],
                row["official"],
            ),
        )

        daily_output = {
            "date": talk_date,
            "timezone": "Asia/Taipei",
            "model": normalize_text(digest.get("model")),
            "source_generated_at": normalize_text(
                digest.get("generated_at")
            ),
            "talk_count": len(sorted_talks),
            "talks": sorted_talks,
        }

        existing_output = load_optional_json_object(daily_file)

        if existing_output != daily_output:
            write_json(daily_file, daily_output)
            written_files.append(daily_file)

    return written_files


def write_central_bank_digest_status(
    digest: dict[str, Any],
    run_at: datetime,
    error_message: str = "",
) -> None:
    write_json(
        CENTRAL_BANK_STATUS_FILE,
        {
            "status": "failed" if error_message else "success",
            "timezone": "Asia/Taipei",
            "utc_offset": "+08:00",
            "run_at": format_taipei_time(run_at),
            "model": digest.get("model", ""),
            "input_headline_count": digest.get(
                "input_headline_count", 0
            ),
            "talk_count": digest.get("talk_count", 0),
            "error": error_message,
            **(
                {"last_success_at": format_taipei_time(run_at)}
                if not error_message
                else {}
            ),
        },
    )

def build_history_digest_index(
    run_at: datetime,
) -> dict[str, Any]:
    """建立歷史新聞索引，供前端按日期載入每日 Gemini Digest。"""

    items: list[dict[str, Any]] = []

    for file_path in DIGEST_DIRECTORY.glob(
        "[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.json"
    ):
        date_text = file_path.stem

        # 只接受YYYY-MM-DD.json
        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}",
            date_text,
        ):
            continue

        try:
            datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError:
            continue

        try:
            digest = read_json_object(file_path)
        except (OSError, ValueError, TypeError):
            continue

        categories = digest.get("categories", [])
        event_count = 0

        if isinstance(categories, list):
            for category in categories:
                if (
                    isinstance(category, dict)
                    and isinstance(category.get("news"), list)
                ):
                    event_count += len(category["news"])

        items.append(
            {
                "date": date_text,
                "file": "./" + file_path.as_posix(),
                "period_start": normalize_text(
                    digest.get("period_start")
                ),
                "period_end": normalize_text(
                    digest.get("period_end")
                ),
                "overview": normalize_text(
                    digest.get("overview")
                ),
                "event_count": event_count,
            }
        )

    # 日期由新到舊排列
    items.sort(
        key=lambda item: item["date"],
        reverse=True,
    )

    index = {
        "generated_at": format_taipei_time(run_at),
        "timezone": "Asia/Taipei",
        "count": len(items),
        "earliest_date": (
            items[-1]["date"] if items else ""
        ),
        "latest_date": (
            items[0]["date"] if items else ""
        ),
        "items": items,
    }

    write_json(HISTORY_INDEX_FILE, index)

    return index
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

        breakfast = fetch_wallstreetcn_breakfast(run_at)
        breakfast_prompt = build_wallstreetcn_breakfast_prompt(
            breakfast
        )

        prompt = build_user_prompt(
            compact_input=compact_input,
            input_count=len(cleaned_items),
            included_count=included_count,
            period_start=period_start,
            period_end=period_end,
            breakfast_prompt=breakfast_prompt,
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

        central_bank_digest = generate_central_bank_digest(
            api_key=api_key,
            model=model,
            run_at=run_at,
        )
        write_central_bank_digest_status(
            central_bank_digest,
            run_at,
        )

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

        central_bank_daily_files = (
            build_central_bank_daily_archives(
                central_bank_digest
            )
        )

        write_json(daily_digest_file, digest)
        write_json(LATEST_DIGEST_FILE, digest)
        write_json(debug_file, debug_output)
        write_json(LATEST_DEBUG_FILE, debug_output)
        write_json(
            CENTRAL_BANK_LATEST_DIGEST_FILE,
            central_bank_digest,
        )
        history_index = build_history_digest_index(run_at)

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
        print(
            "Central bank compact daily archives updated: "
            f"{len(central_bank_daily_files)}"
        )

        for archive_file in central_bank_daily_files:
            print(f"- {archive_file}")

        print(
            "Central bank 90-day talk count: "
            f"{central_bank_digest.get('talk_count', 0)}"
        )

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