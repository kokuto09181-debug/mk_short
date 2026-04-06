"""
メインパイプライン
1日3本 × 日英2チャンネル = 6動画を自動生成・投稿する

実行フロー:
  1. Notionから未制作の偉人を取得
  2. 偉人が不足していれば Claude に追加生成させる
  3. 偉人ごとに日本語・英語の脚本を生成
  4. TTS音声合成
  5. Pexels から背景画像取得
  6. MoviePy で動画組み立て
  7. YouTube にアップロード（日英チャンネル）
  8. Notion に制作完了を記録
"""

import json
import logging
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

load_dotenv(encoding="utf-8", override=True)

# src モジュールのパスを通す
sys.path.insert(0, str(Path(__file__).parent))

from content_generator import ContentGenerator
from image_fetcher import ImageFetcher
from notion_client import NotionFigureClient
from tts_generator import TTSGenerator
from uploader import YouTubeUploader
from video_creator import VideoCreator

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
DATA_DIR = Path(__file__).parent.parent / "data"
PENDING_DIR = DATA_DIR / "pending_uploads"

FIELDS = [
    "科学者・発明家",
    "女性の先駆者",
    "芸術家・文化人",
    "医師・思想家",
    "地方の英雄・反骨者",
    "外交官・先駆的外国人",
]


def load_config() -> dict:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


class Pipeline:
    def __init__(self, dry_run: bool = False):
        """
        dry_run=True の場合、動画生成まで行うがYouTubeへのアップロードは行わない
        コンポーネントは実際に使用するタイミングで初期化（レイジーロード）
        """
        self.config = load_config()
        self.dry_run = dry_run
        self._notion = None
        self._generator = None
        self._tts = None
        self._image_fetcher = None
        self._video_creator = None
        self._uploader_jp = None

    @property
    def notion(self):
        if self._notion is None:
            self._notion = NotionFigureClient()
        return self._notion

    @property
    def generator(self):
        if self._generator is None:
            self._generator = ContentGenerator()
        return self._generator

    @property
    def tts(self):
        if self._tts is None:
            self._tts = TTSGenerator()
        return self._tts

    @property
    def image_fetcher(self):
        if self._image_fetcher is None:
            self._image_fetcher = ImageFetcher()
        return self._image_fetcher

    @property
    def video_creator(self):
        if self._video_creator is None:
            self._video_creator = VideoCreator()
        return self._video_creator

    @property
    def uploader_jp(self):
        if self._uploader_jp is None:
            self._uploader_jp = YouTubeUploader(channel="japanese")
        return self._uploader_jp


    # ─────────────────────────────────────────
    # メイン実行
    # ─────────────────────────────────────────

    def run_daily(self, videos_per_day: Optional[int] = None, name_filter: str = ""):
        """1日分の動画を生成・投稿する"""
        n = videos_per_day or self.config["content"]["videos_per_day"]
        logger.info(f"=== パイプライン開始: {n}本 × 日英2チャンネル ===")

        # BGMが未ダウンロードなら自動取得
        self._ensure_bgm()

        # 前回アップロード失敗分をリトライ
        if not self.dry_run:
            self._retry_pending_uploads()

        # 中断等でproducingのまま残ったものをリセット
        self.notion.reset_stale_producing()

        # 偉人のストックを確認・補充
        self._ensure_figure_stock(needed=n)

        # 未制作の偉人を取得
        fetch_limit = 100 if name_filter else n
        figures = self.notion.get_pending_figures(limit=fetch_limit)
        if name_filter:
            figures = [f for f in figures if f.get("name_ja") == name_filter]
        if not figures:
            logger.error("pending の偉人が見つかりません。シードデータを登録してください。")
            return

        results = []
        for figure in figures:
            result = self._process_figure(figure)
            results.append(result)

        # サマリー
        success = sum(1 for r in results if r["success"])
        logger.info(f"=== 完了: {success}/{len(results)} 本成功 ===")
        return results

    # ─────────────────────────────────────────
    # 1偉人分の処理
    # ─────────────────────────────────────────

    def _process_figure(self, figure: dict) -> dict:
        page_id = figure["page_id"]
        name_ja = figure["name_ja"]
        logger.info(f"--- 処理開始: {name_ja} ---")

        # 制作中フラグ
        self.notion.mark_producing(page_id)

        work_dir = tempfile.mkdtemp(prefix=f"shorts_{name_ja[:8]}_")
        try:
            # 1. 脚本生成（日本語のみ）
            long_script_ja = figure.get("long_script_ja", "") or ""
            longform_video_id = figure.get("longform_video_id", "") or ""

            script_ja_json = figure.get("script_ja", "")

            # Notionに既存脚本があれば優先使用
            if script_ja_json:
                try:
                    script_ja = json.loads(script_ja_json)
                    script_ja["language"] = "ja"
                    script_ja["figure_name_ja"] = figure.get("name_ja", "")
                    script_ja["figure_name_en"] = figure.get("name_en", "")
                    script_ja["figure_era"] = figure.get("era", "")
                    script_ja["figure_field"] = figure.get("field", "")
                    logger.info(f"Notionの既存脚本を使用: {name_ja}")
                except (json.JSONDecodeError, KeyError):
                    logger.warning("脚本JSONのパースに失敗。再生成します")
                    script_ja_json = ""

            # 既存脚本がない場合のみ生成
            if not script_ja_json:
                if long_script_ja and longform_video_id:
                    # 長編あり: Hookから派生生成
                    logger.info(f"長編Hookからショート脚本を派生生成: {name_ja}")
                    script_ja = self.generator.generate_short_from_longform_hook(figure, long_script_ja)
                else:
                    # 長編なし: 通常生成
                    script_ja = self.generator.generate_script(figure, language="ja")

            # 2. 背景画像取得 - まずWikipediaで偉人の実際の画像を取得
            img_dir = os.path.join(work_dir, "images")
            wiki_paths = self.image_fetcher.fetch_wikipedia_images(
                figure["name_ja"], figure["name_en"], img_dir
            )
            # 先頭のWikipedia画像を顔写真（ポートレート）として使用
            portrait_path = wiki_paths[0] if wiki_paths else None
            image_paths = list(wiki_paths)

            # 不足分をPexelsのキーワード検索で補完
            if len(image_paths) < 4:
                keywords = script_ja.get("search_keywords_en", ["Japan", "history"])
                need = max(3, 6 - len(image_paths))
                pexels_paths = self.image_fetcher.fetch_images(keywords, img_dir, count=need)
                image_paths.extend(pexels_paths)
            if not image_paths:
                logger.warning("画像取得失敗。デフォルトキーワードで再試行")
                image_paths = self.image_fetcher.fetch_images(
                    ["ancient Japan", "traditional"], img_dir, count=3
                )

            # 3. 日本語動画生成・アップロード
            jp_video_id = self._produce_and_upload(
                script=script_ja,
                figure=figure,
                image_paths=image_paths,
                portrait_path=portrait_path,
                work_dir=os.path.join(work_dir, "ja"),
                language="ja",
                longform_video_id=longform_video_id,
            )

            # 4. Notion に完了記録
            self.notion.mark_done(
                page_id=page_id,
                title_ja=script_ja.get("title", ""),
                title_en="",
                jp_video_id=jp_video_id,
                en_video_id="",
            )

            logger.info(f"完了: {name_ja} | JP={jp_video_id}")
            return {
                "success": True,
                "name_ja": name_ja,
                "jp_video_id": jp_video_id,
            }

        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"エラー: {name_ja}\n{tb}")
            self.notion.mark_error(page_id, str(e)[:500])
            return {"success": False, "name_ja": name_ja, "error": str(e)}

        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _produce_and_upload(
        self,
        script: dict,
        figure: dict,
        image_paths: list[str],
        work_dir: str,
        language: str,
        portrait_path: Optional[str] = None,
        longform_video_id: str = "",
    ) -> str:
        """TTS → 動画生成 → YouTube アップロード。video_id を返す"""
        os.makedirs(work_dir, exist_ok=True)
        lang_label = "日本語" if language == "ja" else "英語"
        logger.info(f"[{lang_label}] 動画制作開始")

        # TTS（OpenAI TTS 優先、なければ edge-tts）
        narration = self.generator.build_narration(script)
        tts_lang_key = "japanese" if language == "ja" else "english"
        tts_gen = TTSGenerator()
        tts_gen.tts_config = self.config["tts"][tts_lang_key]
        tts_gen.provider = self.config["tts"][tts_lang_key].get("provider", "edge_tts")

        audio_path, duration = tts_gen.generate_with_speed(
            narration, work_dir
        )
        logger.info(f"[{lang_label}] 音声（BGM込み）: {duration:.1f}秒")

        # 動画生成（顔写真レイアウト + 字幕 + テキストアニメ）
        video_path = os.path.join(work_dir, "output.mp4")
        self.video_creator.create_video(
            script=script,
            audio_path=audio_path,
            image_paths=image_paths,
            output_path=video_path,
            narration=narration,
            portrait_path=portrait_path,
            longform_video_id=longform_video_id if language == "ja" else "",
        )

        # サムネイル
        thumb_path = os.path.join(work_dir, "thumbnail.jpg")
        self.video_creator.create_thumbnail(script, thumb_path)

        if self.dry_run:
            logger.info(f"[DRY RUN] アップロードスキップ: {video_path}")
            return "dry_run"

        # YouTube アップロード
        uploader = self.uploader_jp if language == "ja" else self.uploader_en
        description = self.generator.build_description(
            script, longform_video_id=longform_video_id if language == "ja" else ""
        )

        try:
            video_id = uploader.upload(
                video_path=video_path,
                title=script["title"],
                description=description,
                thumbnail_path=thumb_path,
            )

            # プレイリストに追加（日本語のみ）
            if language == "ja" and longform_video_id and video_id:
                # 長編と同じプレイリストにショートを追加
                playlist_id = figure.get("playlist_id", "")
                if playlist_id:
                    uploader.add_to_playlist(playlist_id, video_id)

            return video_id
        except Exception as upload_err:
            logger.warning(f"[{lang_label}] アップロード失敗。保留フォルダに保存: {upload_err}")
            self._save_pending(
                video_path=video_path,
                thumb_path=thumb_path,
                language=language,
                title=script["title"],
                description=description,
                figure=figure,
                script=script,
            )
            raise

    # ─────────────────────────────────────────
    # BGM 自動ダウンロード
    # ─────────────────────────────────────────

    def _ensure_bgm(self):
        """BGMが未ダウンロードの場合、Internet Archive（パブリックドメイン）から自動取得する"""
        bgm_root = DATA_DIR / "bgm"
        moods = ["inspiring", "empowering", "classical", "calm", "dramatic"]
        missing = [m for m in moods if not list((bgm_root / m).glob("*.mp3")) and not list((bgm_root / m).glob("*.ogg"))] if bgm_root.exists() else moods

        if not missing:
            return

        logger.info(f"BGM未ダウンロード: {missing} → Internet Archive（パブリックドメイン）から取得")
        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, str(Path(__file__).parent.parent / "scripts" / "download_bgm.py")],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                logger.info("BGMダウンロード完了")
            else:
                logger.warning(f"BGMダウンロード失敗（合成BGMで続行）: {result.stderr[-200:]}")
        except Exception as e:
            logger.warning(f"BGMダウンロードエラー（合成BGMで続行）: {e}")

    # ─────────────────────────────────────────
    # 保留アップロード管理
    # ─────────────────────────────────────────

    def _save_pending(self, video_path, thumb_path, language, title, description, figure, script):
        """アップロード失敗時に動画と必要情報をローカルに保存する"""
        import datetime
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        slot = PENDING_DIR / f"{figure['name_ja'][:8]}_{language}_{ts}"
        slot.mkdir()

        dst_video = slot / "output.mp4"
        dst_thumb = slot / "thumbnail.jpg"
        shutil.copy2(video_path, dst_video)
        if os.path.exists(thumb_path):
            shutil.copy2(thumb_path, dst_thumb)

        meta = {
            "language": language,
            "title": title,
            "description": description,
            "page_id": figure.get("page_id", ""),
            "name_ja": figure.get("name_ja", ""),
            "name_en": figure.get("name_en", ""),
        }
        with open(slot / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        logger.info(f"保留保存: {slot}")

    def _retry_pending_uploads(self):
        """保留フォルダ内の動画を順番にアップロード再試行する"""
        if not PENDING_DIR.exists():
            return

        slots = sorted(PENDING_DIR.iterdir())
        if not slots:
            return

        logger.info(f"=== 保留アップロード: {len(slots)}件 ===")
        for slot in slots:
            meta_path = slot / "metadata.json"
            video_path = slot / "output.mp4"
            thumb_path = slot / "thumbnail.jpg"

            if not meta_path.exists() or not video_path.exists():
                logger.warning(f"保留フォルダが不完全のためスキップ: {slot.name}")
                continue

            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)

            language = meta.get("language", "ja")
            logger.info(f"保留再試行: {meta['name_ja']} / {meta['title']}")

            try:
                uploader = self.uploader_jp
                video_id = uploader.upload(
                    video_path=str(video_path),
                    title=meta["title"],
                    description=meta["description"],
                    thumbnail_path=str(thumb_path) if thumb_path.exists() else None,
                )
                logger.info(f"保留再試行成功: {video_id}")
                shutil.rmtree(slot)
            except Exception as e:
                logger.warning(f"保留再試行失敗（次回に持ち越し）: {e}")

    # ─────────────────────────────────────────
    # Notion ストック管理
    # ─────────────────────────────────────────

    def _ensure_figure_stock(self, needed: int, min_stock: int = 15):
        """pending が min_stock を下回ったら Claude に補充を依頼する"""
        pending = self.notion.get_pending_figures(limit=100)
        stock = len(pending)
        logger.info(f"偉人ストック: {stock} 件 pending")

        if stock >= min_stock:
            return

        logger.info(f"ストック不足（{stock}件）。Claudeに補充依頼...")
        existing = self.notion.get_all_names_ja()
        import random
        field = random.choice(FIELDS)

        new_figures = self.generator.generate_new_figures(
            field=field,
            era_range="飛鳥時代〜昭和",
            existing_names=existing,
            count=15,
        )
        added = self.notion.add_figures(new_figures)
        logger.info(f"偉人 {len(added)} 件を Notion に追加")

    # ─────────────────────────────────────────
    # 初期セットアップ
    # ─────────────────────────────────────────

    def seed_notion(self):
        """data/figures_seed.json を Notion DB に一括投入する"""
        seed_path = DATA_DIR / "figures_seed.json"
        with open(seed_path, encoding="utf-8") as f:
            figures = json.load(f)

        existing = self.notion.get_all_names_ja()
        new_figures = [f for f in figures if f["name_ja"] not in existing]

        if not new_figures:
            logger.info("シードデータはすでに全件登録済みです")
            return

        added = self.notion.add_figures(new_figures)
        logger.info(f"シード投入完了: {len(added)} 件")

    def setup_notion_db(self, parent_page_id: str):
        """Notion DB を初期作成する（初回のみ）"""
        db_id = self.notion.setup_database(parent_page_id)
        print(f"\nNotion DB 作成完了!")
        print(f"以下の値を GitHub Secret に設定してください:")
        print(f"  NOTION_DATABASE_ID = {db_id}")
        return db_id


# ─────────────────────────────────────────────
# CLI エントリポイント
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="YouTube Shorts 自動運営パイプライン")
    subparsers = parser.add_subparsers(dest="command")

    # run: 通常実行
    run_parser = subparsers.add_parser("run", help="動画を生成してアップロード")
    run_parser.add_argument("--dry-run", action="store_true", help="アップロードをスキップ")
    run_parser.add_argument("--count", type=int, help="生成本数（デフォルト: 設定値）")
    run_parser.add_argument("--name", type=str, default="", help="特定の偉人名（日本語）を指定して1件のみ処理")

    # seed: Notionにシードデータ投入
    seed_parser = subparsers.add_parser("seed", help="Notionに偉人シードデータを投入")

    # setup-db: NotionDB初期作成
    setup_parser = subparsers.add_parser("setup-db", help="Notion DBを新規作成")
    setup_parser.add_argument("parent_page_id", help="Notionの親ページID")

    args = parser.parse_args()

    if args.command == "run":
        pipeline = Pipeline(dry_run=args.dry_run)
        pipeline.run_daily(videos_per_day=args.count, name_filter=getattr(args, "name", ""))

    elif args.command == "seed":
        pipeline = Pipeline(dry_run=True)
        pipeline.seed_notion()

    elif args.command == "setup-db":
        pipeline = Pipeline(dry_run=True)
        pipeline.setup_notion_db(args.parent_page_id)

    else:
        parser.print_help()
