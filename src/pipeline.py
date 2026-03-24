"""
メインパイプライン
全モジュールを統合して1本のYouTube Shorts動画を生成・投稿する

実行フロー:
1. スクリプト生成 (Claude API)
2. 音声生成 (edge-tts)
3. 背景素材取得 (Pexels API)
4. 動画合成 (MoviePy)
5. YouTube投稿 (YouTube Data API)
6. ログ保存
"""
import json
import logging
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from src.content_generator import generate_script
from src.tts_generator import generate_voice, build_subtitle_segments
from src.background_fetcher import fetch_background_video, fetch_background_image
from src.video_assembler import create_short_video
from src.youtube_uploader import upload_short, build_description

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def run_pipeline(
    niche: str = None,
    language: str = None,
    upload: bool = True,
    dry_run: bool = False,
) -> dict:
    """
    パイプライン全体を実行する

    Args:
        niche: コンテンツニッチ ("facts", "motivation", "money")
        language: "ja" or "en"
        upload: YouTubeにアップロードするか
        dry_run: True の場合、動画生成のみ（アップロードしない）

    Returns:
        実行結果の辞書
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"short_{timestamp}"
    output_dir = Path(config.OUTPUT_DIR) / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "run_id": run_id,
        "timestamp": timestamp,
        "status": "started",
        "steps": {},
    }

    try:
        # ==========================================
        # Step 1: スクリプト生成
        # ==========================================
        logger.info("Step 1: スクリプト生成中...")
        script = generate_script(niche=niche, language=language)
        result["steps"]["script"] = {"status": "ok", "title": script["title"]}
        result["script"] = script
        logger.info(f"タイトル: {script['title']}")

        # スクリプトをファイルに保存
        script_path = output_dir / "script.json"
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)

        # ==========================================
        # Step 2: 音声生成
        # ==========================================
        logger.info("Step 2: 音声生成中...")
        audio_path = str(output_dir / "voice.mp3")
        voice_result = generate_voice(
            text=script["full_script"],
            output_path=audio_path,
            language=language or config.CONTENT_LANGUAGE,
        )
        result["steps"]["tts"] = {
            "status": "ok",
            "duration": voice_result["duration"],
            "audio_path": audio_path,
        }
        logger.info(f"音声生成完了: {voice_result['duration']:.1f}秒")

        # 字幕セグメントを構築（日本語は文字数ベース、英語は単語ベース）
        lang = language or config.CONTENT_LANGUAGE
        words_per_seg = 12 if lang == "ja" else 6
        subtitle_segments = build_subtitle_segments(
            voice_result["subtitles"],
            words_per_segment=words_per_seg,
        )

        # ==========================================
        # Step 3: 背景素材取得
        # ==========================================
        logger.info("Step 3: 背景素材取得中...")
        bg_keyword = script.get("background_keyword", "nature")
        background_path = None

        # まず動画を試みる
        bg_video_path = str(output_dir / "background.mp4")
        background_path = fetch_background_video(
            keyword=bg_keyword,
            output_path=bg_video_path,
            duration_min=30,
        )

        if background_path:
            result["steps"]["background"] = {"status": "ok", "type": "video", "keyword": bg_keyword}
            logger.info(f"背景動画取得: {bg_keyword}")
        else:
            # フォールバック: 画像
            bg_img_path = str(output_dir / "background.jpg")
            background_path = fetch_background_image(
                keyword=bg_keyword,
                output_path=bg_img_path,
            )
            if background_path:
                result["steps"]["background"] = {"status": "ok", "type": "image", "keyword": bg_keyword}
                logger.info(f"背景画像取得: {bg_keyword}")
            else:
                result["steps"]["background"] = {"status": "fallback", "type": "gradient"}
                logger.info("背景: グラデーション（フォールバック）")

        # ==========================================
        # Step 4: 動画合成
        # ==========================================
        logger.info("Step 4: 動画合成中...")
        video_path = str(output_dir / f"{run_id}.mp4")
        create_short_video(
            audio_path=audio_path,
            subtitle_segments=subtitle_segments,
            title=script["title"],
            thumbnail_text=script.get("thumbnail_text", script["title"]),
            output_path=video_path,
            background_path=background_path,
            duration=voice_result["duration"],
        )
        result["steps"]["video"] = {"status": "ok", "path": video_path}
        logger.info(f"動画生成完了: {video_path}")

        # ==========================================
        # Step 5: YouTube投稿
        # ==========================================
        if upload and not dry_run:
            logger.info("Step 5: YouTube投稿中...")
            description = build_description(script, niche=niche or config.CONTENT_NICHE)
            upload_result = upload_short(
                video_path=video_path,
                title=script["title"],
                description=description,
                tags=script.get("tags", []),
            )
            result["steps"]["upload"] = upload_result
            result["youtube_url"] = upload_result.get("url")
            logger.info(f"投稿結果: {upload_result['status']}")
        else:
            result["steps"]["upload"] = {"status": "skipped"}
            logger.info("Step 5: アップロードスキップ")

        result["status"] = "completed"
        logger.info(f"パイプライン完了: {run_id}")

    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        logger.error(f"パイプラインエラー: {e}")
        logger.error(traceback.format_exc())

    finally:
        # 結果をJSONで保存
        log_path = output_dir / "result.json"
        with open(log_path, "w", encoding="utf-8") as f:
            # tracebackは長いので省略
            save_result = {k: v for k, v in result.items() if k != "traceback"}
            json.dump(save_result, f, ensure_ascii=False, indent=2)
        logger.info(f"結果を保存: {log_path}")

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="YouTube Shorts 自動生成パイプライン")
    parser.add_argument(
        "--niche",
        choices=["facts", "motivation", "money"],
        default=None,
        help="コンテンツニッチ（省略時はconfigの設定値）",
    )
    parser.add_argument(
        "--language",
        choices=["ja", "en"],
        default=None,
        help="言語（省略時はconfigの設定値）",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="YouTubeアップロードをスキップ",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="動画生成のみ（アップロードしない）",
    )
    args = parser.parse_args()

    result = run_pipeline(
        niche=args.niche,
        language=args.language,
        upload=not args.no_upload,
        dry_run=args.dry_run,
    )

    print("\n" + "=" * 50)
    print("実行結果:")
    print(f"  ステータス: {result['status']}")
    if result.get("script"):
        print(f"  タイトル: {result['script']['title']}")
    if result.get("youtube_url"):
        print(f"  YouTube URL: {result['youtube_url']}")
    print("=" * 50)
