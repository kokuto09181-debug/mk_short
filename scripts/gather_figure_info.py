"""
Wikipedia 情報収集スクリプト
Notion の全偉人についてWikipedia（日本語）から情報を収集し、
research_data フィールドに保存する。

収集範囲:
  - 日本語Wikipedia: 記事全文（制限なし）
  - 関連記事（日本語）: 本文リンク先から最大 RELATED_ARTICLE_COUNT 件
  - 合計上限: TOTAL_MAX_CHARS 文字（Sonnet 200kコンテキスト内に収まる範囲）

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
WIKI_HEADERS = {"User-Agent": "mk_short/1.0 (https://github.com/kokuto09181-debug/mk_short; research bot)"}

RELATED_ARTICLE_COUNT = 5    # 関連記事の最大取得件数
RELATED_ARTICLE_CHARS = 8000 # 関連記事1件あたりの最大文字数
TOTAL_MAX_CHARS = 100_000    # 合計最大文字数（Sonnet 200kトークンの約半分）
REQUEST_DELAY = 1.5          # Wikipedia API リクエスト間隔（秒）


def fetch_wikipedia_extract(title: str, lang: str = "ja") -> str:
    """指定タイトルのWikipediaページから本文全文を取得する"""
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
        resp = requests.get(api_url, params=params, headers=WIKI_HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        for page_data in pages.values():
            if "missing" in page_data:
                return ""
            return page_data.get("extract", "")
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
        resp = requests.get(api_url, params=params, headers=WIKI_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("query", {}).get("search", [])
        if results:
            return results[0]["title"]
    except Exception as e:
        logger.warning(f"Wikipedia検索失敗 [{lang}] {query}: {e}")
    return ""


def fetch_related_article_titles(title: str, count: int = 10) -> list[str]:
    """日本語Wikipediaの記事からリンクされている関連記事タイトルを取得する"""
    params = {
        "action": "query",
        "titles": title,
        "prop": "links",
        "pllimit": count * 3,  # 人物・出来事ページに絞るため多めに取得してフィルタ
        "plnamespace": 0,      # 記事ページのみ
        "format": "json",
        "redirects": True,
    }
    try:
        resp = requests.get(WIKI_JA_API, params=params, headers=WIKI_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        for page_data in pages.values():
            links = page_data.get("links", [])
            titles = [l["title"] for l in links]
            # 曖昧さ回避ページ・カテゴリ系を除外し、適度な長さのタイトルのみ
            filtered = [
                t for t in titles
                if not any(skip in t for skip in ["曖昧さ回避", "一覧", "Category", "Wikipedia"])
                and 2 <= len(t) <= 20
            ]
            return filtered[:count]
    except Exception as e:
        logger.warning(f"関連記事リンク取得失敗: {title}: {e}")
    return []


def gather_figure_info(figure: dict) -> str:
    """
    偉人1名分の情報をWikipediaから収集してテキストとして返す。
    - 日本語Wikipedia全文
    - 英語Wikipedia全文
    - 関連記事（最大 RELATED_ARTICLE_COUNT 件）
    - 合計 TOTAL_MAX_CHARS 文字で打ち切り
    """
    name_ja = figure.get("name_ja", "")
    name_en = figure.get("name_en", "")
    era = figure.get("era", "")
    field = figure.get("field", "")
    notes = figure.get("notes", "")

    lines = [
        f"【偉人名】{name_ja}",
        f"【時代】{era}　【分野】{field}",
    ]
    if notes:
        lines.append(f"【特記事項】{notes}")
    lines.append("")

    total_chars = len("\n".join(lines))

    # ── 日本語Wikipedia ──────────────────────
    ja_extract = fetch_wikipedia_extract(name_ja, lang="ja")
    if not ja_extract and name_ja:
        found_title = search_wikipedia(name_ja, lang="ja")
        if found_title and found_title != name_ja:
            time.sleep(REQUEST_DELAY)
            ja_extract = fetch_wikipedia_extract(found_title, lang="ja")

    if ja_extract:
        remaining = TOTAL_MAX_CHARS - total_chars - 200  # ヘッダー分を確保
        ja_text = ja_extract[:remaining] if len(ja_extract) > remaining else ja_extract
        lines.append("=== Wikipedia（日本語）===")
        lines.append(ja_text)
        if len(ja_extract) > len(ja_text):
            lines.append("（以下省略）")
        lines.append("")
        total_chars += len(ja_text)
        logger.info(f"  JA Wikipedia: {len(ja_extract)}文字取得（{len(ja_text)}文字使用）")
    else:
        logger.warning(f"日本語Wikipedia未発見: {name_ja}")
        lines.append("=== Wikipedia（日本語）: 記事なし ===")
        lines.append("")

    time.sleep(REQUEST_DELAY)

    # ── 関連記事（日本語） ──────────────────
    if total_chars < TOTAL_MAX_CHARS and ja_extract:
        related_titles = fetch_related_article_titles(name_ja, count=RELATED_ARTICLE_COUNT * 2)
        time.sleep(REQUEST_DELAY)

        fetched_count = 0
        for rel_title in related_titles:
            if fetched_count >= RELATED_ARTICLE_COUNT:
                break
            if total_chars >= TOTAL_MAX_CHARS:
                break

            time.sleep(REQUEST_DELAY)
            rel_extract = fetch_wikipedia_extract(rel_title, lang="ja")
            if not rel_extract:
                continue

            remaining = min(RELATED_ARTICLE_CHARS, TOTAL_MAX_CHARS - total_chars - 200)
            if remaining <= 100:
                break

            rel_text = rel_extract[:remaining]
            lines.append(f"=== 関連: {rel_title} ===")
            lines.append(rel_text)
            if len(rel_extract) > len(rel_text):
                lines.append("（省略）")
            lines.append("")
            total_chars += len(rel_text)
            fetched_count += 1
            logger.info(f"  関連記事「{rel_title}」: {len(rel_text)}文字使用")

    # Wikipedia本文が1件も取得できていない場合は空文字を返す（保存しない）
    if not ja_extract:
        logger.warning(f"  Wikipedia本文なし。保存スキップ: {name_ja}")
        return ""

    result = "\n".join(lines)
    logger.info(f"  合計: {len(result)}文字")
    return result


def run(limit: int = 50, force_all: bool = False):
    """メイン処理"""
    client = NotionFigureClient()
    client.ensure_longform_properties()

    if force_all:
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

        if i < len(figures):
            time.sleep(REQUEST_DELAY)

    logger.info(f"=== 完了: {success}/{len(figures)} 件収集・保存 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wikipedia偉人情報収集（拡張版）")
    parser.add_argument("--limit", type=int, default=50, help="最大処理件数（デフォルト: 50）")
    parser.add_argument("--all", dest="force_all", action="store_true",
                        help="全偉人を強制再収集（デフォルト: 未収集のみ）")
    args = parser.parse_args()

    run(limit=args.limit, force_all=args.force_all)
