"""
長編動画アップロードスクリプト
render_done 状態の長編動画をスケジュール配信で YouTube にアップロードする。

・配信時刻は settings.yaml の upload.schedule_times_jst を使用
・今から1時間以降の最初の空きスロットに順番に割り当て
・アップロード完了後に Notion の longform_status → uploaded / longform_video_id を更新

使用方法:
  python scripts/upload_longform.py           # 既定: 最大3件
  python scripts/upload_longform.py --limit 5 # 最大N件
"""

import argparse
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from notion_client import NotionFigureClient
from uploader import YouTubeUploader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "longform_output"
CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def safe_dirname(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\s]', "_", name)


def next_schedule_slots(schedule_times_jst: list, count: int, start_from: datetime) -> list:
    """
    start_from の1時間後以降で、最初の count 個のスロット時刻を返す（UTC aware datetime）。
    """
    min_time = (start_from + timedelta(hours=1)).astimezone(JST)
    times_hm = sorted([tuple(map(int, t.split(":"))) for t in schedule_times_jst])

    slots = []
    check_date = min_time.date()

    while len(slots) < count:
        for h, m in times_hm:
            slot = datetime(check_date.year, check_date.month, check_date.day, h, m, tzinfo=JST)
            if slot >= min_time:
                slots.append(slot.astimezone(timezone.utc))
                if len(slots) >= count:
                    return slots
        check_date += timedelta(days=1)
        min_time = datetime(check_date.year, check_date.month, check_date.day, tzinfo=JST)

    return slots


def generate_thumbnail(video_path: str, output_path: str) -> Optional[str]:
    """動画の5秒地点フレームを 1280×720 JPEG サムネイルとして抽出する"""
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", "5",
                "-i", video_path,
                "-vframes", "1",
                "-s", "1280x720",
                "-q:v", "2",
                output_path,
            ],
            check=True, capture_output=True,
        )
        logger.info(f"サムネイル生成: {output_path}")
        return output_path
    except Exception as e:
        logger.warning(f"サムネイル生成失敗（スキップ）: {e}")
        return None


def parse_title_and_description(long_script: str) -> tuple:
    """long_script_ja ヘッダーからタイトル・説明文・タグを抽出して返す"""
    title = ""
    description = ""
    tags_str = ""
    for line in long_script.split("\n"):
        line = line.strip()
        if line.startswith("タイトル:"):
            title = line.split(":", 1)[1].strip()
        elif line.startswith("説明文:"):
            description = line.split(":", 1)[1].strip()
        elif line.startswith("タグ:"):
            tags_str = line.split(":", 1)[1].strip()
        # ヘッダーは最初の区切り線より前
        if "==============================" in line:
            break
    return title, description, tags_str


def run(limit: int = 3, name: str = ""):
    notion = NotionFigureClient()
    config = load_config()
    schedule_times = config["upload"]["schedule_times_jst"]

    # render_done の偉人を取得（uploading 中のものは除外して2重実行を防止）
    data = notion.query_figures({
        "property": "longform_status",
        "select": {"equals": "render_done"},
    })
    all_figures = [notion._page_to_figure(p) for p in data]
    if name:
        figures = [f for f in all_figures if f.get("name_ja") == name]
    else:
        figures = all_figures
    logger.info(f"アップロード待ち: {len(figures)} 件")

    if not figures:
        logger.info("アップロード対象なし。完了。")
        return

    figures = figures[:limit]

    # 配信スケジュールスロットを生成
    slots = next_schedule_slots(schedule_times, len(figures), datetime.now(timezone.utc))
    logger.info(f"配信スロット: {[s.astimezone(JST).strftime('%m/%d %H:%M JST') for s in slots]}")

    uploader = YouTubeUploader(channel="japanese")
    success = 0

    for figure, publish_at in zip(figures, slots):
        name_ja = figure.get("name_ja", "不明")
        page_id = figure["page_id"]

        work_dir = OUTPUT_DIR / safe_dirname(name_ja)
        video_path = work_dir / "output.mp4"

        # 既に video_id が入っていればスキップ（2重アップロード防止）
        if figure.get("longform_video_id"):
            logger.warning(f"既にアップロード済みのためスキップ: {name_ja} (video_id={figure['longform_video_id']})")
            notion.save_longform_video_id(page_id, figure["longform_video_id"])  # status を uploaded に戻す
            continue

        if not video_path.exists():
            logger.error(f"動画ファイルなし: {video_path}")
            notion.mark_longform_render_error(
                page_id, f"動画ファイルが見つかりません: {video_path}"
            )
            continue

        # アップロード中フラグを先に立てる（並列実行・再実行による2重アップロード防止）
        notion.mark_longform_uploading(page_id)

        logger.info(
            f"アップロード: {name_ja}"
            f" → {publish_at.astimezone(JST).strftime('%Y-%m-%d %H:%M')} JST"
        )

        # サムネイル生成（動画から抽出）
        thumb_path = str(work_dir / "thumbnail.jpg")
        if not Path(thumb_path).exists():
            generate_thumbnail(str(video_path), thumb_path)
        thumb = thumb_path if Path(thumb_path).exists() else None

        # タイトル・説明文を long_script_ja ヘッダーから取得
        long_script = figure.get("long_script_ja", "")
        title, description, tags_str = parse_title_and_description(long_script)

        if not title:
            title = f"{name_ja}の知られざる生涯【長編】"
        if not description:
            description = f"{name_ja}の生涯と功績を詳しく解説します。"

        extra_tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        base_tags = ["偉人", "日本史", "歴史", "長編"]
        all_tags = list(dict.fromkeys(base_tags + extra_tags))  # 重複除去

        # Wikipedia リンクを説明文に追加
        name_en = figure.get("name_en", "")
        description += f"\n\n【参考・出典】\n・Wikipedia「{name_ja}」\n  https://ja.wikipedia.org/wiki/{name_ja}"
        if name_en:
            description += f"\n・Wikipedia \"{name_en}\"\n  https://en.wikipedia.org/wiki/{name_en.replace(' ', '_')}"

        try:
            video_id = uploader.upload(
                video_path=str(video_path),
                title=title,
                description=description,
                thumbnail_path=thumb,
                tags=all_tags,
                publish_at=publish_at,
            )
            notion.save_longform_video_id(page_id, video_id)
            success += 1
            logger.info(
                f"完了: {name_ja} → video_id={video_id}"
                f" 配信予定: {publish_at.astimezone(JST).strftime('%Y-%m-%d %H:%M JST')}"
            )
        except Exception as e:
            logger.error(f"アップロード失敗: {name_ja}: {e}")
            # uploading → render_done に戻して次回再試行できるようにする（render_error にしない）
            notion._ensure_error_log_property()
            notion._patch(f"pages/{page_id}", {
                "properties": {
                    "longform_status": {"select": {"name": "render_done"}},
                    "error_log": {"rich_text": [{"text": {"content": f"アップロード失敗: {str(e)[:400]}"}}]},
                }
            })

    logger.info(f"=== 完了: {success}/{len(figures)} 件 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="長編動画スケジュールアップロード")
    parser.add_argument("--limit", type=int, default=3, help="最大処理件数（デフォルト: 3）")
    parser.add_argument("--name", type=str, default="", help="特定の偉人名（日本語）を指定して1件のみ処理")
    args = parser.parse_args()
    run(limit=args.limit, name=args.name)
