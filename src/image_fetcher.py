"""
素材画像取得モジュール
Wikipedia API（優先）+ Pexels API（補完）から画像を取得する
"""

import logging
import os
import random
from pathlib import Path
from typing import Optional

import requests
import yaml
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"

PEXELS_API_BASE = "https://api.pexels.com/v1"
WIKIPEDIA_API_JP = "https://ja.wikipedia.org/w/api.php"
WIKIPEDIA_API_EN = "https://en.wikipedia.org/w/api.php"


def load_config() -> dict:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


class ImageFetcher:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("PEXELS_API_KEY", "")
        self.config = load_config()
        self.img_config = self.config["images"]
        self.session = requests.Session()
        self.session.headers.update({"Authorization": self.api_key})
        # Wikipedia用セッション（認証不要）
        self._wiki_session = requests.Session()
        self._wiki_session.headers.update({"User-Agent": "mk_short/1.0 (educational)"})

    # ─────────────────────────────────────────
    # Wikipedia 画像取得
    # ─────────────────────────────────────────

    def fetch_wikipedia_images(
        self, name_ja: str, name_en: str, output_dir: str
    ) -> list[str]:
        """Wikipedia APIで偉人の画像を取得する。日英両方を試みる"""
        os.makedirs(output_dir, exist_ok=True)
        paths = []

        for api_url, name in [
            (WIKIPEDIA_API_JP, name_ja),
            (WIKIPEDIA_API_EN, name_en),
        ]:
            if not name:
                continue
            try:
                new_paths = self._fetch_wiki_images_for(api_url, name, output_dir, len(paths))
                paths.extend(new_paths)
                if len(paths) >= 4:
                    break
            except Exception as e:
                logger.warning(f"Wikipedia画像取得失敗 ({name}): {e}")

        logger.info(f"Wikipedia画像: {len(paths)}枚")
        return paths

    def _fetch_wiki_images_for(
        self, api_url: str, name: str, output_dir: str, offset: int
    ) -> list[str]:
        """指定言語のWikipediaから画像を取得する"""
        params = {
            "action": "query",
            "titles": name,
            "prop": "pageimages|images",
            "pithumbsize": 1200,
            "imlimit": 10,
            "format": "json",
            "redirects": 1,
        }
        resp = self._wiki_session.get(api_url, params=params, timeout=15)
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})

        paths = []
        for page_id, page in pages.items():
            if page_id == "-1":
                continue

            # pageimages のサムネイル（最も関連性が高い画像）
            thumb = page.get("thumbnail", {})
            if thumb.get("source"):
                path = self._download_from_url(
                    thumb["source"], output_dir, f"wiki_{offset + len(paths):02d}"
                )
                if path:
                    paths.append(path)

            # images リストから人物写真を追加で取得
            for img_meta in page.get("images", [])[:5]:
                title = img_meta.get("title", "")
                lower = title.lower()
                # SVG・アイコン・地図は除外
                if any(x in lower for x in [".svg", "icon", "map", "logo", "flag"]):
                    continue
                if not any(lower.endswith(ext) for ext in [".jpg", ".jpeg", ".png"]):
                    continue
                img_url = self._get_wiki_image_url(api_url, title)
                if img_url:
                    path = self._download_from_url(
                        img_url, output_dir, f"wiki_{offset + len(paths):02d}"
                    )
                    if path:
                        paths.append(path)
                if len(paths) >= 3:
                    break

        return paths

    def _get_wiki_image_url(self, api_url: str, title: str) -> Optional[str]:
        """WikipediaのファイルタイトルからURLを取得する"""
        try:
            params = {
                "action": "query",
                "titles": title,
                "prop": "imageinfo",
                "iiprop": "url",
                "format": "json",
            }
            resp = self._wiki_session.get(api_url, params=params, timeout=10)
            resp.raise_for_status()
            pages = resp.json().get("query", {}).get("pages", {})
            for page in pages.values():
                imageinfo = page.get("imageinfo", [])
                if imageinfo:
                    return imageinfo[0].get("url")
        except Exception as e:
            logger.debug(f"画像URL取得失敗 {title}: {e}")
        return None

    def _download_from_url(
        self, url: str, output_dir: str, filename: str
    ) -> Optional[str]:
        """URLから直接画像をダウンロードして保存パスを返す"""
        try:
            ext = url.split("?")[0].rsplit(".", 1)[-1].lower()
            if ext not in ("jpg", "jpeg", "png", "webp"):
                ext = "jpg"
            output_path = os.path.join(output_dir, f"{filename}.{ext}")
            resp = self._wiki_session.get(url, timeout=30, stream=True)
            resp.raise_for_status()
            with open(output_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info(f"Wikipedia画像保存: {output_path}")
            return output_path
        except Exception as e:
            logger.warning(f"ダウンロード失敗 {url[:60]}: {e}")
            return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
    )
    def search(self, keywords: list[str], count: int = 5) -> list[dict]:
        """キーワードで画像を検索し、メタデータリストを返す"""
        query = " ".join(keywords[:2])  # 最大2キーワード使用
        params = {
            "query": query,
            "orientation": self.img_config.get("orientation", "portrait"),
            "per_page": self.img_config.get("per_page", 15),
            "size": "large",
        }

        logger.info(f"Pexels 検索: query='{query}'")
        response = self.session.get(f"{PEXELS_API_BASE}/search", params=params, timeout=15)
        response.raise_for_status()

        photos = response.json().get("photos", [])
        if not photos:
            logger.warning(f"画像が見つかりません: {query}。フォールバック検索を実行")
            return self._fallback_search(count)

        # ランダムにシャッフルして指定枚数返す
        random.shuffle(photos)
        selected = photos[:count]
        logger.info(f"{len(selected)} 枚の画像を取得")
        return selected

    def _fallback_search(self, count: int) -> list[dict]:
        """汎用キーワードでフォールバック検索"""
        fallback_queries = ["Japan", "abstract background", "nature", "city night"]
        query = random.choice(fallback_queries)
        params = {
            "query": query,
            "orientation": "portrait",
            "per_page": 15,
        }
        response = self.session.get(f"{PEXELS_API_BASE}/search", params=params, timeout=15)
        response.raise_for_status()
        photos = response.json().get("photos", [])
        random.shuffle(photos)
        return photos[:count]

    def download(self, photo: dict, output_dir: str, index: int = 0) -> str:
        """画像をダウンロードして保存パスを返す"""
        os.makedirs(output_dir, exist_ok=True)
        url = photo["src"].get("large2x") or photo["src"]["large"]
        ext = url.split("?")[0].rsplit(".", 1)[-1] or "jpg"
        output_path = os.path.join(output_dir, f"bg_{index:02d}.{ext}")

        logger.info(f"画像ダウンロード: {url[:60]}...")
        response = self.session.get(url, timeout=30, stream=True)
        response.raise_for_status()

        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        logger.info(f"保存: {output_path}")
        return output_path

    def fetch_images(self, keywords: list[str], output_dir: str, count: int = 5) -> list[str]:
        """検索 + ダウンロードをまとめて実行。保存パスのリストを返す"""
        photos = self.search(keywords, count)
        paths = []
        for i, photo in enumerate(photos):
            try:
                path = self.download(photo, output_dir, index=i)
                paths.append(path)
            except Exception as e:
                logger.warning(f"画像 {i} のダウンロード失敗: {e}")
        return paths


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    fetcher = ImageFetcher()
    paths = fetcher.fetch_images(["Japan nature", "forest"], "/tmp/img_test", count=3)
    print("取得画像:", paths)
