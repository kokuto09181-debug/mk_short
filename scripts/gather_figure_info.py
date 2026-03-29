"""
Wikipedia 情報収集スクリプト
Notion の全偉人についてWikipedia（日本語・英語）から情報を収集し、
research_data フィールドに保存する。

使用方法:
  python scripts/gather_figure_info.py           # 未収集の全偉人を処理
  python scripts/gather_figure_info.py --all     # 全偉人を再収集（上書き）
  python scripts/gather_figure_info.py --limit 5 # 最大5件処理
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from notion_client import NotionFigureClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

WIKI_JA_API = "https://ja.wikipedia.org/w/api.php"
WIKI_EN_API = "https://en.wikipedia.org/w/api.php"

JA_EXTRACT_MAX = 6000   # 日本語Wikipediaから最大6000文字
EN_EXTRACT_MAX = 2000   # 英語Wikipediaから最大2000文字（補足）
REQUEST_DELAY = 1.5     # Wikipedia API リクエスト間隔（秒）


def fetch_wikipedia_extract(title: str, lang: str = "ja") -> str:
    """指定タイトルのWikipediaページから本文を取得する"""
    api_url = WIKI_JA_API if lang == "ja" else WIKI_EN_API
    params = {
        "action": "query",
        "titles": title,
        "prop": "extracts",
        "explaintext": True,
        "exsectionformat": "plain",
        "format": "json",
        "redirects": True,
    }
    try:
        resp = requests.get(api_url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        for page_data in pages.values():
            if "missing" in page_data:
                return ""
            extract = page_data.get("extract", "")
            return extract
    except Exception as e:
        logger.warning(f"Wikipedia取得失敗 [{lang}] {title}: {e}")
        return ""


def search_wikipedia(query: str, lang: str = "ja") -> str:
    """検索クエリでWikipediaを検索し、最初のヒットページ名を返す"""
    api_url = WIKI_JA_API if lang == "ja" else WIKI_EN_API
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": 1,
        "format": "json",
    }
    try:
        resp = requests.get(api_url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("query", {}).get("search", [])
        if results:
            return results[0]["title"]
    except Exception as e:
        logger.warning(f"Wikipedia検索失敗 [{lang}] {query}: {e}")
    return ""


def gather_figure_info(figure: dict) -> str:
    """偉人1名分の情報をWikipediaから収集してテキストとして返す"""
    name_ja = figure.get("name_ja", "")
    name_en = figure.get("name_en", "")
    era = figure.get("era", "")
    field = figure.get("field", "")
    notes = figure.get("notes", "")

    lines = [f"【偉人名】{name_ja}（{name_en}）", f"【時代】{era}　【分野】{field}"]
    if notes:
        lines.append(f"【特記事項】{notes}")
    lines.append("")

    # 日本語Wikipedia
    ja_extract = fetch_wikipedia_extract(name_ja, lang="ja")
    if not ja_extract and name_ja:
        # 直接タイトルで見つからない場合は検索
        found_title = search_wikipedia(name_ja, lang="ja")
        if found_title and found_title != name_ja:
            time.sleep(REQUEST_DELAY)
            ja_extract = fetch_wikipedia_extract(found_title, lang="ja")

    if ja_extract:
        truncated = ja_extract[:JA_EXTRACT_MAX]
        lines.append("=== Wikipedia（日本語）===")
        lines.append(truncated)
        if len(ja_extract) > JA_EXTRACT_MAX:
            lines.append("（以下省略）")
        lines.append("")
    else:
        logger.warning(f"日本語Wikipedia未発見: {name_ja}")
        lines.append("=== Wikipedia（日本語）: 記事なし ===")
        lines.append("")

    time.sleep(REQUEST_DELAY)

    # 英語Wikipedia（補足情報）
    en_title = name_en if name_en else name_ja
    en_extract = fetch_wikipedia_extract(en_title, lang="en")
    if not en_extract and name_en:
        found_title = search_wikipedia(name_en, lang="en")
        if found_title:
            time.sleep(REQUEST_DELAY)
            en_extract = fetch_wikipedia_extract(found_title, lang="en")

    if en_extract:
        truncated_en = en_extract[:EN_EXTRACT_MAX]
        lines.append("=== Wikipedia (English) ===")
        lines.append(truncated_en)
        if len(en_extract) > EN_EXTRACT_MAX:
            lines.append("(truncated)")
        lines.append("")
    else:
        lines.append("=== Wikipedia (English): No article found ===")
        lines.append("")

    return "\n".join(lines)


def run(limit: int = 50, force_all: bool = False):
    """メイン処理"""
    client = NotionFigureClient()

    # Notionに新フィールドが存在しない場合は追加
    client.ensure_longform_properties()

    if force_all:
        # 全偉人を取得
        pages = client.query_figures()
        figures = [client._page_to_figure(p) for p in pages]
        logger.info(f"全偉人対象: {len(figures)} 件")
    else:
        figures = client.get_figures_without_research(limit=limit)

    if not figures:
        logger.info("収集対象の偉人がいません。完了。")
        return

    success = 0
    for i, figure in enumerate(figures, 1):
        name_ja = figure.get("name_ja", "不明")
        page_id = figure["page_id"]
        logger.info(f"[{i}/{len(figures)}] 収集中: {name_ja}")

        research_text = gather_figure_info(figure)
        if research_text.strip():
            client.save_research_data(page_id, research_text)
            success += 1
            logger.info(f"  → 保存完了 ({len(research_text)}文字)")
        else:
            logger.warning(f"  → 情報取得できず: {name_ja}")

        # レート制限対策
        if i < len(figures):
            time.sleep(REQUEST_DELAY)

    logger.info(f"=== 完了: {success}/{len(figures)} 件収集・保存 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wikipedia偉人情報収集")
    parser.add_argument("--limit", type=int, default=50, help="最大処理件数（デフォルト: 50）")
    parser.add_argument("--all", dest="force_all", action="store_true",
                        help="全偉人を強制再収集（デフォルト: 未収集のみ）")
    args = parser.parse_args()

    run(limit=args.limit, force_all=args.force_all)
