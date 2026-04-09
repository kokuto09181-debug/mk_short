"""
長編動画アップロードスクリプト
render_done 状態の長編動画をスケジュール配信で YouTube にアップロードする。

【配信モード】
  --mode slot   (デフォルト) settings.yaml のスロットに1件ずつ順番に割り当て
  --mode fixed  全件を同じ日時に配信（--time で時刻指定、省略時は次スロット）

使用方法:
  python scripts/upload_longform.py                        # スロットモード 最大3件
  python scripts/upload_longform.py --limit 5              # スロットモード 最大5件
  python scripts/upload_longform.py --mode fixed           # 全件を次スロット日時に配信
  python scripts/upload_longform.py --mode fixed --time 20:00          # 本日20:00 JST に全件配信
  python scripts/upload_longform.py --mode fixed --time 2025-08-01 20:00  # 指定日時に全件配信
  python scripts/upload_longform.py --name 緒方洪庵        # 特定偉人のみ
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
    """【スロットモード】start_from の1時間後以降で count 個のスロットを順番に返す（UTC）。"""
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


def fixed_schedule_slots(count: int, fixed_time_str: Optional[str], schedule_times_jst: list) -> list:
    """【同時配信モード】全件を同じ日時にスケジュールした UTC datetime リストを返す。

    Args:
        count: 動画本数
        fixed_time_str: 指定日時文字列。以下の形式を受け付ける:
            None         → settings.yaml の次スロット（1時間後以降の最初のスロット）
            "HH:MM"      → 本日または翌日の HH:MM JST
            "YYYY-MM-DD HH:MM" → 指定日時 JST
        schedule_times_jst: settings.yaml のスロット一覧（fixed_time_str=None 時に使用）
    """
    if fixed_time_str is None:
        # settings.yaml の次スロット1つを全件に使う
        slots_one = next_schedule_slots(schedule_times_jst, 1, datetime.now(timezone.utc))
        publish_at = slots_one[0]
    else:
        fixed_time_str = fixed_time_str.strip()
        try:
            if len(fixed_time_str) <= 5:
                # "HH:MM" のみ → 本日の該当時刻（1時間後未満なら翌日）
                h, m = map(int, fixed_time_str.split(":"))
                now_jst = datetime.now(JST)
                candidate = now_jst.replace(hour=h, minute=m, second=0, microsecond=0)
                if candidate < now_jst + timedelta(hours=1):
                    candidate += timedelta(days=1)
                publish_at = candidate.astimezone(timezone.utc)
            else:
                # "YYYY-MM-DD HH:MM" → 指定日時
                dt = datetime.strptime(fixed_time_str, "%Y-%m-%d %H:%M")
                publish_at = dt.replace(tzinfo=JST).astimezone(timezone.utc)
        except ValueError as e:
            raise ValueError(
                f"--time の形式が不正です: '{fixed_time_str}'\n"
                f"  正しい形式: 'HH:MM' または 'YYYY-MM-DD HH:MM'\n  詳細: {e}"
            )

    jst_str = publish_at.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")
    logger.info(f"同時配信モード: 全 {count} 件を {jst_str} に配信")
    return [publish_at] * count


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


def run(limit: int = 3, name: str = "", mode: str = "slot", fixed_time: Optional[str] = None):
    """
    Args:
        mode: "slot"  → settings.yaml スロットに1件ずつ順番配信（デフォルト）
              "fixed" → 全件を同じ日時に配信（fixed_time で時刻指定）
        fixed_time: mode="fixed" 時の配信日時。"HH:MM" or "YYYY-MM-DD HH:MM" (JST)
    """
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
    if mode == "fixed":
        slots = fixed_schedule_slots(len(figures), fixed_time, schedule_times)
    else:
        slots = next_schedule_slots(schedule_times, len(figures), datetime.now(timezone.utc))
        logger.info(f"スロットモード: {[s.astimezone(JST).strftime('%m/%d %H:%M JST') for s in slots]}")

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
    parser = argparse.ArgumentParser(
        description="長編動画スケジュールアップロード",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
配信モード:
  slot  (デフォルト): settings.yaml のスロットに1件ずつ順番割り当て
  fixed             : 全件を同じ日時に配信

例:
  python scripts/upload_longform.py                              # スロット 最大3件
  python scripts/upload_longform.py --limit 10                   # スロット 最大10件
  python scripts/upload_longform.py --mode fixed                 # 次スロット日時に全件同時配信
  python scripts/upload_longform.py --mode fixed --time 20:00    # 本日20:00 JST に全件同時配信
  python scripts/upload_longform.py --mode fixed --time "2025-08-01 20:00"  # 指定日時に全件同時配信
  python scripts/upload_longform.py --name 緒方洪庵              # 特定偉人のみ（スロットモード）
        """,
    )
    parser.add_argument("--limit", type=int, default=3, help="最大処理件数（デフォルト: 3）")
    parser.add_argument("--name", type=str, default="", help="特定の偉人名（日本語）を指定して1件のみ処理")
    parser.add_argument(
        "--mode", type=str, default="slot", choices=["slot", "fixed"],
        help="配信モード: slot=スロット順（デフォルト）/ fixed=全件同時配信",
    )
    parser.add_argument(
        "--time", type=str, default=None,
        metavar="HH:MM or 'YYYY-MM-DD HH:MM'",
        help="fixed モード時の配信日時 JST（省略時は次スロット）",
    )
    args = parser.parse_args()
    run(limit=args.limit, name=args.name, mode=args.mode, fixed_time=args.time)
