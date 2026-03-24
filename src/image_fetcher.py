"""
素材画像取得モジュール
Pexels API（無料）から縦向き高品質画像を取得する
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
