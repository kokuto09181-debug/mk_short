"""
素材画像取得モジュール

優先順:
  1. Wikipedia 日本語版 — pageimages サムネイル + 記事内画像
  2. Wikipedia 英語版  — 同上（日本語で足りない場合）
  3. DuckDuckGo 画像検索 — それでも足りない場合の最終手段
"""

import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

WIKIPEDIA_API_JP = "https://ja.wikipedia.org/w/api.php"
WIKIPEDIA_API_EN = "https://en.wikipedia.org/w/api.php"

# 除外するWikipedia画像のキーワード（ロゴ・アイコン・地図など）
_BAD_IMAGE_KEYWORDS = (
    "logo", "icon", "flag", "map", "commons", "wikidata",
    "blank", "edit", "question", "wikisource", "disambig",
    "portal", "button", "arrow", "star", "award", "seal",
    "signature", "coat_of_arms", "symbol", "emblem",
)


class ImageFetcher:
    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "mk_short/1.0 (educational)"
        })

    # ─────────────────────────────────────────
    # メインエントリポイント
    # ─────────────────────────────────────────

    def fetch_images_for_figure(
        self,
        name_ja: str,
        name_en: str,          # 後方互換のため引数は残すが使わない
        output_dir: str,
        count: int = 5,
        search_keywords: Optional[list[str]] = None,
    ) -> list[str]:
        """
        偉人の画像を最大 count 枚取得して保存パスのリストを返す。

        取得順:
          1. Wikipedia 日本語版（pageimages + 記事内画像）
          2. DuckDuckGo 画像検索（Wikipedia で足りない場合）
        """
        os.makedirs(output_dir, exist_ok=True)
        paths: list[str] = []
        seen_urls: set[str] = set()

        # 1. Wikipedia 日本語版
        if name_ja and len(paths) < count:
            new = self._fetch_wiki_article_images(
                WIKIPEDIA_API_JP, name_ja, output_dir,
                max_count=count - len(paths),
                start_idx=len(paths),
                seen_urls=seen_urls,
            )
            paths.extend(new)
            logger.info(f"  Wikipedia: {len(new)}枚")

        # 2. Wikimedia Commons（Wikipedia で足りない場合）
        if len(paths) < count and name_ja:
            new = self._fetch_wikimedia_commons(
                query=name_ja,
                output_dir=output_dir,
                max_count=count - len(paths),
                start_idx=len(paths),
                seen_urls=seen_urls,
            )
            paths.extend(new)
            if new:
                logger.info(f"  Wikimedia Commons: {len(new)}枚")

        # 3. DuckDuckGo フォールバック
        if len(paths) < count:
            # まず日本語名だけで検索、それでも足りなければ英語キーワードを追加
            new = self._fetch_duckduckgo(
                keywords=[name_ja] if name_ja else [],
                output_dir=output_dir,
                count=count - len(paths),
                start_idx=len(paths),
            )
            paths.extend(new)
            if new:
                logger.info(f"  DuckDuckGo: {len(new)}枚")

        if len(paths) < count and search_keywords:
            new = self._fetch_duckduckgo(
                keywords=search_keywords,
                output_dir=output_dir,
                count=count - len(paths),
                start_idx=len(paths),
            )
            paths.extend(new)
            if new:
                logger.info(f"  DuckDuckGo(EN): {len(new)}枚")

        logger.info(f"  画像取得合計: {len(paths)}枚")
        return paths

    # 後方互換ラッパー
    def fetch_wikipedia_images(self, name_ja: str, name_en: str, output_dir: str) -> list[str]:
        return self.fetch_images_for_figure(name_ja, name_en, output_dir, count=3)

    # ─────────────────────────────────────────
    # Wikipedia 画像取得
    # ─────────────────────────────────────────

    def _fetch_wiki_article_images(
        self,
        api_url: str,
        name: str,
        output_dir: str,
        max_count: int,
        start_idx: int,
        seen_urls: set[str],
    ) -> list[str]:
        """
        Wikipedia 記事から最大 max_count 枚を取得する。

        レート制限対策:
          - APIコールは最大2回（pageimages+images 一括 / imageinfo 一括）
          - 画像ダウンロード間は 0.5s スリープ
          - 429 を受けたら即座に中断し DuckDuckGo に委ねる
          - 連続リクエストを避けるため API 呼び出し間も 0.5s スリープ
        """
        # ── Step 1: pageimages + images リストを1回のAPIコールで取得 ──
        params = {
            "action": "query",
            "titles": name,
            "prop": "pageimages|images",
            "pithumbsize": 1200,
            "imlimit": 20,          # 取りすぎない
            "format": "json",
            "redirects": 1,
        }
        try:
            resp = self._session.get(api_url, params=params, timeout=15)
            if resp.status_code == 429:
                logger.warning(f"Wikipedia 429（Step1） → DuckDuckGo にフォールバック")
                return []
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"Wikipedia API エラー ({name}): {e}")
            return []

        pages = resp.json().get("query", {}).get("pages", {})
        if not pages:
            return []
        page = next(iter(pages.values()))
        if str(page.get("pageid", "-1")) == "-1":
            logger.info(f"  Wikipediaにページなし: {name}")
            return []

        paths: list[str] = []

        # メインサムネイル（最優先）
        thumb_url = page.get("thumbnail", {}).get("source", "")
        if thumb_url and thumb_url not in seen_urls:
            p = self._download(thumb_url, output_dir, f"wiki_{start_idx:02d}")
            if p:
                seen_urls.add(thumb_url)
                paths.append(p)
            time.sleep(0.5)

        if len(paths) >= max_count:
            return paths

        # 記事内画像リストをフィルタリング
        img_titles = [
            img["title"] for img in page.get("images", [])
            if not self._is_bad_image(img["title"])
        ]
        if not img_titles:
            return paths

        # ── Step 2: imageinfo 一括取得（APIコール1回、最大8件） ──
        time.sleep(0.5)  # Step1 との間隔を空ける
        info_params = {
            "action": "query",
            "titles": "|".join(img_titles[:8]),
            "prop": "imageinfo",
            "iiprop": "url|size",
            "format": "json",
        }
        try:
            resp2 = self._session.get(api_url, params=info_params, timeout=15)
            if resp2.status_code == 429:
                logger.warning("Wikipedia 429（Step2） → 現状の画像で継続")
                return paths
            resp2.raise_for_status()
            info_pages = resp2.json().get("query", {}).get("pages", {})
        except Exception as e:
            logger.warning(f"imageinfo 取得失敗 ({name}): {e}")
            return paths

        for pdata in info_pages.values():
            if len(paths) >= max_count:
                break
            infos = pdata.get("imageinfo", [])
            if not infos:
                continue
            img_url = infos[0].get("url", "")
            width   = infos[0].get("width", 0)
            height  = infos[0].get("height", 0)
            if width < 200 or height < 200:
                continue
            if img_url.lower().endswith(".svg"):
                continue
            if img_url and img_url not in seen_urls:
                idx = start_idx + len(paths)
                p = self._download(img_url, output_dir, f"wiki_{idx:02d}")
                if p:
                    seen_urls.add(img_url)
                    paths.append(p)
                time.sleep(0.5)

        return paths

    def _is_bad_image(self, title: str) -> bool:
        """ロゴ・アイコン・地図など不要な画像を除外する"""
        t = title.lower()
        if t.endswith(".svg"):
            return True
        return any(k in t for k in _BAD_IMAGE_KEYWORDS)

    def _download(self, url: str, output_dir: str, filename: str) -> Optional[str]:
        """画像を1枚ダウンロードして保存パスを返す。失敗時は None"""
        try:
            ext = url.split("?")[0].rsplit(".", 1)[-1].lower()
            if ext not in ("jpg", "jpeg", "png", "webp"):
                ext = "jpg"
            output_path = os.path.join(output_dir, f"{filename}.{ext}")
            resp = self._session.get(url, timeout=30, stream=True)
            if resp.status_code == 429:
                return None
            resp.raise_for_status()
            with open(output_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info(f"  保存: {Path(output_path).name}")
            return output_path
        except Exception as e:
            logger.debug(f"  ダウンロード失敗 {url[:60]}: {e}")
            return None

    # ─────────────────────────────────────────
    # Wikimedia Commons 画像検索
    # ─────────────────────────────────────────

    def _fetch_wikimedia_commons(
        self,
        query: str,
        output_dir: str,
        max_count: int,
        start_idx: int,
        seen_urls: set[str],
    ) -> list[str]:
        """Wikimedia Commons から画像を検索して取得する（APIコール2回以内）"""
        COMMONS_API = "https://commons.wikimedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": f"{query} filetype:bitmap",
            "srnamespace": 6,   # File: namespace
            "srlimit": max_count * 3,
            "format": "json",
        }
        try:
            resp = self._session.get(COMMONS_API, params=params, timeout=15)
            if resp.status_code == 429:
                return []
            resp.raise_for_status()
            items = resp.json().get("query", {}).get("search", [])
        except Exception as e:
            logger.debug(f"  Commons 検索失敗: {e}")
            return []

        if not items:
            return []

        titles = [item["title"] for item in items[:max_count * 2]]
        time.sleep(0.5)

        info_params = {
            "action": "query",
            "titles": "|".join(titles[:8]),
            "prop": "imageinfo",
            "iiprop": "url|size",
            "format": "json",
        }
        try:
            resp2 = self._session.get(COMMONS_API, params=info_params, timeout=15)
            if resp2.status_code == 429:
                return []
            resp2.raise_for_status()
            pages = resp2.json().get("query", {}).get("pages", {})
        except Exception as e:
            logger.debug(f"  Commons imageinfo 失敗: {e}")
            return []

        paths: list[str] = []
        for pdata in pages.values():
            if len(paths) >= max_count:
                break
            infos = pdata.get("imageinfo", [])
            if not infos:
                continue
            img_url = infos[0].get("url", "")
            width   = infos[0].get("width", 0)
            height  = infos[0].get("height", 0)
            if width < 200 or height < 200:
                continue
            if img_url.lower().endswith(".svg"):
                continue
            if self._is_bad_image(pdata.get("title", "")):
                continue
            if img_url and img_url not in seen_urls:
                idx = start_idx + len(paths)
                p = self._download(img_url, output_dir, f"commons_{idx:02d}")
                if p:
                    seen_urls.add(img_url)
                    paths.append(p)
                time.sleep(0.5)

        return paths

    # ─────────────────────────────────────────
    # DuckDuckGo 画像検索フォールバック
    # ─────────────────────────────────────────

    def _fetch_duckduckgo(
        self,
        keywords: list[str],
        output_dir: str,
        count: int,
        start_idx: int,
    ) -> list[str]:
        """DuckDuckGo 画像検索で補完する（duckduckgo-search パッケージ必要）"""
        # ddgs（旧 duckduckgo_search）を試みる
        DDGS = None
        try:
            from ddgs import DDGS
        except ImportError:
            try:
                from duckduckgo_search import DDGS
            except ImportError:
                logger.warning("ddgs 未インストール → pip install ddgs")
                return []

        query = " ".join(k for k in keywords if k)
        if not query:
            return []

        logger.info(f"  DuckDuckGo 画像検索: '{query}'")
        paths: list[str] = []

        try:
            # フィルターなし（size/type フィルターが 403 の原因になるため）
            results = list(DDGS().images(
                query,
                max_results=count * 3,
            ))
            random.shuffle(results)

            for result in results:
                if len(paths) >= count:
                    break
                url = result.get("image", "")
                if not url:
                    continue
                ext = url.split("?")[0].rsplit(".", 1)[-1].lower()
                if ext not in ("jpg", "jpeg", "png", "webp"):
                    ext = "jpg"
                idx = start_idx + len(paths)
                output_path = os.path.join(output_dir, f"search_{idx:02d}.{ext}")
                try:
                    r = requests.get(
                        url, timeout=10, stream=True,
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    if r.status_code == 200:
                        with open(output_path, "wb") as f:
                            for chunk in r.iter_content(8192):
                                f.write(chunk)
                        paths.append(output_path)
                except Exception:
                    pass

        except Exception as e:
            logger.warning(f"DuckDuckGo 検索失敗: {e}")

        return paths
