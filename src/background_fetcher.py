"""
背景素材取得モジュール
Pexels API (無料) から動画・画像を取得する
フォールバック: グラデーション背景を自動生成
"""
import os
import random
import requests
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config


PEXELS_VIDEO_URL = "https://api.pexels.com/videos/search"
PEXELS_PHOTO_URL = "https://api.pexels.com/v1/search"


def fetch_background_video(keyword: str, output_path: str, duration_min: int = 30) -> str | None:
    """
    Pexels APIから縦型動画を取得してダウンロードする

    Returns:
        ダウンロードしたファイルパス、失敗時はNone
    """
    if not config.PEXELS_API_KEY:
        return None

    headers = {"Authorization": config.PEXELS_API_KEY}
    params = {
        "query": keyword,
        "orientation": "portrait",
        "size": "medium",
        "per_page": 10,
    }

    try:
        response = requests.get(PEXELS_VIDEO_URL, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        videos = data.get("videos", [])
        if not videos:
            return None

        # 適切な長さの動画を探す
        suitable = [v for v in videos if v.get("duration", 0) >= duration_min]
        if not suitable:
            suitable = videos  # フォールバック

        video = random.choice(suitable)

        # HD縦型ファイルを優先
        video_files = video.get("video_files", [])
        portrait_files = [f for f in video_files if f.get("height", 0) > f.get("width", 0)]
        if not portrait_files:
            portrait_files = video_files

        # 解像度でソート（高い順）
        portrait_files.sort(key=lambda f: f.get("height", 0), reverse=True)
        target_file = portrait_files[0]

        # ダウンロード
        video_url = target_file["link"]
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        r = requests.get(video_url, stream=True, timeout=30)
        r.raise_for_status()

        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

        return output_path

    except Exception as e:
        print(f"Pexels動画取得エラー: {e}")
        return None


def fetch_background_image(keyword: str, output_path: str) -> str | None:
    """
    Pexels APIから縦型画像を取得してダウンロードする

    Returns:
        ダウンロードしたファイルパス、失敗時はNone
    """
    if not config.PEXELS_API_KEY:
        return None

    headers = {"Authorization": config.PEXELS_API_KEY}
    params = {
        "query": keyword,
        "orientation": "portrait",
        "size": "large",
        "per_page": 15,
    }

    try:
        response = requests.get(PEXELS_PHOTO_URL, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        photos = data.get("photos", [])
        if not photos:
            return None

        photo = random.choice(photos)
        img_url = photo["src"]["portrait"]  # 縦型

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        r = requests.get(img_url, stream=True, timeout=30)
        r.raise_for_status()

        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

        return output_path

    except Exception as e:
        print(f"Pexels画像取得エラー: {e}")
        return None
