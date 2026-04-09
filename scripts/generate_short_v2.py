"""
改善版ショート動画生成スクリプト（v2）

施策1: 結論ファースト型フック + 感情タグ付き脚本
施策2: Ken Burns効果（ズーム・パン）
施策3: Whisper ワードレベル字幕（ASS形式）
施策6: シリーズタグ対応
施策7a: Irodori-TTS（感情統一・固定シード）
画像: Wikipedia + Pexels（B案）

使い方:
  python scripts/generate_short_v2.py --name "関孝和"
  python scripts/generate_short_v2.py --name "関孝和" --dry-run
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "src"))

from dotenv import load_dotenv
load_dotenv(_root / ".env")

from src.content_generator import ContentGenerator
from src.image_fetcher import ImageFetcher
from src.notion_client import NotionFigureClient
from src.tts_generator import TTSGenerator
from src.video_creator_v2 import compose_short_video, create_engagement_card
from src.thumbnail_generator import create_thumbnail

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

OUTPUT_BASE = Path(__file__).parent.parent / "data" / "short_v2_output"


def generate_v2_short(
    figure: dict,
    output_dir: Path,
    tts: TTSGenerator,
    content_gen: ContentGenerator,
    dry_run: bool = False,
) -> str:
    """1偉人分のv2ショート動画を生成する"""
    name_ja = figure.get("name_ja", "不明")
    name_en = figure.get("name_en", "")
    logger.info(f"=== v2ショート生成: {name_ja} ===")

    os.makedirs(output_dir, exist_ok=True)

    # ─── 1. 脚本生成 ───
    logger.info("[1/5] 脚本生成")
    script_path = output_dir / "script.json"
    if script_path.exists():
        logger.info("  既存 script.json を使用（スキップ）")
        with open(script_path, encoding="utf-8") as f:
            script = json.load(f)
    else:
        script = content_gen.generate_script(figure, "ja", prompt_key="script_ja_v2")
        if not script:
            logger.error("脚本生成失敗")
            return ""
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)

    hook = script.get("hook", "")
    sections = script.get("sections", [])
    cta = script.get("cta", "")
    series_tag = script.get("series_tag", "")

    logger.info(f"  タイトル: {script.get('title', '')}")
    logger.info(f"  シリーズ: {series_tag}")
    logger.info(f"  セクション数: {len(sections)}")

    # ─── 2. TTS音声生成 ───
    logger.info("[2/5] TTS音声生成")
    audio_dir = output_dir / "audio"
    os.makedirs(audio_dir, exist_ok=True)

    narration_parts = []
    if hook:
        narration_parts.append({"text": hook, "emotion": "Surprise"})
    for sec in sections:
        narration_parts.append({
            "text": sec.get("content", ""),
            "emotion": sec.get("emotion", "Neutral"),
        })
    if cta:
        narration_parts.append({"text": cta, "emotion": "Neutral"})

    _per_emotion_providers = ("sbv2", "irodori")
    is_per_emotion = tts.provider in _per_emotion_providers
    audio_segments = []
    part_durations = []
    voice_final = audio_dir / "voice.mp3"

    if voice_final.exists():
        logger.info("  既存 voice.mp3 を使用（TTS スキップ）")
        voice_path = str(voice_final)
        duration = tts.get_duration(voice_path)
        logger.info(f"  最終音声: {duration:.1f}秒")
        if is_per_emotion:
            from pydub import AudioSegment as _AS
            for i in range(len(narration_parts)):
                pp = audio_dir / f"part_{i:02d}.mp3"
                if pp.exists():
                    part_durations.append(len(_AS.from_mp3(str(pp))) / 1000.0)
            if part_durations:
                logger.info(f"  パーツ継続時間: {[f'{d:.1f}s' for d in part_durations]}")
    elif is_per_emotion:
        from pydub import AudioSegment
        for i, part in enumerate(narration_parts):
            part_path = str(audio_dir / f"part_{i:02d}.mp3")
            tts.generate(
                part["text"],
                part_path,
                style=part["emotion"],
                style_weight=5.0,
            )
            seg = AudioSegment.from_mp3(part_path)
            audio_segments.append(seg)
            part_durations.append(len(seg) / 1000.0)
            logger.info(f"  TTS [{i+1}/{len(narration_parts)}]: {part['emotion']} / {len(part['text'])}文字 / {part_durations[-1]:.1f}秒")

        silence = AudioSegment.silent(duration=700)
        combined = audio_segments[0]
        for seg in audio_segments[1:]:
            combined += silence + seg
        combined.export(str(audio_dir / "voice_raw.mp3"), format="mp3")
    else:
        full_text = "".join(p["text"] for p in narration_parts)
        tts.generate(full_text, str(audio_dir / "voice_raw.mp3"))

    if not voice_final.exists():
        if is_per_emotion:
            # generate_with_speed を呼ぶと voice_raw.mp3 が上書きされるため
            # パーツ結合済みの voice_raw.mp3 に直接 速度調整 + BGM混合 を適用する
            import shutil
            combined_path = str(audio_dir / "voice_raw.mp3")
            speed_path = str(audio_dir / "voice_speed.mp3")
            final_path = str(audio_dir / "voice.mp3")
            speed = tts.tts_config.get("speed", 1.0)
            tts.adjust_speed(combined_path, speed_path, speed)
            bgm_config = tts.config.get("bgm", {})
            if bgm_config.get("enabled", True):
                tts.mix_with_bgm(speed_path, final_path)
            else:
                shutil.copy2(speed_path, final_path)
            voice_path = final_path
            duration = tts.get_duration(final_path)
        else:
            voice_path, duration = tts.generate_with_speed(
                "".join(p["text"] for p in narration_parts),
                str(audio_dir),
            )

        logger.info(f"  最終音声: {duration:.1f}秒")

    # ─── 2.5 字幕生成（スクリプトテキスト + デュレーションから直接生成） ───
    logger.info("[2.5/5] 字幕生成")
    ass_path = None
    ass_file = str(audio_dir / "subtitles.ass")

    if Path(ass_file).exists():
        logger.info("  既存 subtitles.ass を使用（スキップ）")
        ass_path = ass_file
    elif part_durations:
        try:
            from src.whisper_subtitles import generate_ass_from_script
            speed = tts.tts_config.get("speed", 1.0)
            generate_ass_from_script(
                narration_parts=narration_parts,
                part_durations=part_durations,
                speed=speed,
                output_path=ass_file,
            )
            ass_path = ass_file
        except Exception as e:
            logger.warning(f"  字幕生成失敗 → MoviePy字幕にフォールバック: {e}")
    else:
        logger.warning("  part_durations が空 → 字幕スキップ")

    # ─── 3. 画像取得（Wikipedia → DuckDuckGo） ───
    logger.info("[3/5] 画像取得（Wikipedia → DuckDuckGo）")
    image_dir = output_dir / "images"
    os.makedirs(image_dir, exist_ok=True)

    total_scenes = 1 + len(sections) + (1 if cta else 0)  # hook + sections + cta
    fetcher = ImageFetcher()
    search_keywords = script.get("search_keywords_en", [])
    all_images = fetcher.fetch_images_for_figure(
        name_ja=name_ja,
        name_en=name_en,
        output_dir=str(image_dir),
        count=total_scenes,
        search_keywords=search_keywords,
    )
    logger.info(f"  画像合計: {len(all_images)}枚")

    # シーン数に合わせて画像を割り当て（不足時はローテーション）
    scene_images = []
    for i in range(total_scenes):
        scene_images.append(all_images[i % len(all_images)] if all_images else "")

    # ─── 3.5. サムネイル生成（動画の最初のフレームとして焼き込む） ───
    # YouTube Shorts はカスタムサムネイルのアップロード不可のため、
    # デザイン済みのサムネイル画像を冒頭フックシーンの背景として使用する。
    # これにより YouTube が最初のフレームをプレビューとして選択したときに
    # サムネイルデザインが表示される。
    logger.info("[3.5/5] サムネイル生成（冒頭フレーム用）")
    thumb_path = output_dir / "thumbnail.jpg"
    if thumb_path.exists():
        logger.info("  既存 thumbnail.jpg を使用")
    else:
        try:
            create_thumbnail(name_ja, script, output_dir)
            logger.info("  thumbnail.jpg を生成")
        except Exception as e:
            logger.warning(f"  サムネイル生成失敗（通常画像で代替）: {e}")
            thumb_path = None

    # ─── 4. 動画合成 ───
    logger.info("[4/5] 動画合成（Ken Burns）")

    # シーン尺 = 対応パーツの音声長（速度調整前）をそのまま使う
    # compose_short_video が合計を音声長に合わせてスケーリングするため
    # 無音分の誤差はそこで自動吸収される
    _fallback_dur = duration / max(len(narration_parts), 1)

    def _scene_dur(pidx: int) -> float:
        if pidx < len(part_durations):
            return part_durations[pidx]
        return _fallback_dur

    scenes = []
    _pidx = 0

    if hook:
        # 冒頭フックシーンの背景 = サムネイル画像
        # → YouTube Shortsが最初のフレームをプレビューに選ぶとサムネイルが表示される
        hook_img = (
            str(thumb_path) if thumb_path and Path(str(thumb_path)).exists()
            else (scene_images[0] if scene_images else "")
        )
        scenes.append({
            "image_path": hook_img,
            "text": hook,
            "duration": _scene_dur(_pidx),
            "emotion": "Surprise",
            "keywords": [],
        })
        _pidx += 1

    for i, sec in enumerate(sections):
        img_idx = min(_pidx, len(scene_images) - 1) if scene_images else -1
        keywords = re.findall(r'\d+[年日人万億円]|\d+', sec.get("content", ""))
        scenes.append({
            "image_path": scene_images[img_idx] if img_idx >= 0 else "",
            "text": sec.get("content", ""),
            "duration": _scene_dur(_pidx),
            "emotion": sec.get("emotion", "Neutral"),
            "keywords": keywords[:3],
        })
        _pidx += 1

    if cta:
        # CTA シーンの背景をエンゲージメントカードに置き換え（問いかけ画面）
        quiz_q = script.get("quiz_question", "")
        if not quiz_q:
            quiz_q = f"{name_ja}のこと、あなたは知っていましたか？"
        engagement_img = str(output_dir / "engagement_card.jpg")
        if not Path(engagement_img).exists():
            try:
                first_img = scene_images[0] if scene_images else ""
                create_engagement_card(
                    quiz_question=quiz_q,
                    output_path=engagement_img,
                    bg_image_path=first_img,
                )
            except Exception as e:
                logger.warning(f"  エンゲージメントカード生成失敗: {e}")
                engagement_img = scene_images[-1] if scene_images else ""

        scenes.append({
            "image_path": engagement_img if Path(engagement_img).exists() else (scene_images[-1] if scene_images else ""),
            "text": cta,
            "duration": _scene_dur(_pidx),
            "emotion": "Neutral",
            "keywords": [],
        })
        _pidx += 1

    output_video = str(output_dir / f"{name_ja}_short_v2.mp4")

    if dry_run:
        logger.info(f"[DRY RUN] 動画合成スキップ: {output_video}")
        logger.info(f"  シーン数: {len(scenes)}")
        for i, s in enumerate(scenes):
            logger.info(f"  シーン{i}: {s['emotion']} / {s['text'][:30]}...")
        return ""

    compose_short_video(
        scenes=scenes,
        audio_path=voice_path,
        output_path=output_video,
        ass_path=ass_path,
    )

    logger.info(f"=== v2ショート生成完了: {output_video} ===")
    return output_video


def main():
    import time as _time

    parser = argparse.ArgumentParser(description="v2ショート動画生成")
    parser.add_argument("--name", default="", help="偉人名（省略時はNotionから未生成を自動選択）")
    parser.add_argument("--count", type=int, default=0, help="生成本数（0=--allと同じ、--name省略時）")
    parser.add_argument("--all", action="store_true", help="未生成の偉人を全件処理")
    parser.add_argument("--dry-run", action="store_true", help="動画合成をスキップ")
    args = parser.parse_args()

    notion = NotionFigureClient()
    notion.ensure_short_v2_properties()

    if args.name:
        candidates = notion.query_figures({
            "property": "Name",
            "title": {"equals": args.name},
        })
        if not candidates:
            print(f"Notionに「{args.name}」が見つかりません")
            sys.exit(1)
        figures = [notion._page_to_figure(candidates[0])]
    else:
        # --all または --count 0 で全件取得
        limit = 9999 if (args.all or args.count == 0) else args.count
        figures = notion.get_pending_v2_figures(limit=limit)
        if not figures:
            print("v2ショート未生成の偉人がありません")
            sys.exit(0)
        print(f"対象: {len(figures)} 件")
        for f in figures:
            print(f"  - {f['name_ja']}")
        print()

    tts = TTSGenerator()
    content_gen = ContentGenerator()

    total = len(figures)
    success = 0
    failed: list[str] = []
    batch_start = _time.time()

    for idx, figure_data in enumerate(figures, 1):
        name_ja = figure_data.get("name_ja", "不明")
        page_id = figure_data.get("page_id", "")
        output_dir = OUTPUT_BASE / name_ja

        t0 = _time.time()
        print(f"[{idx}/{total}] 開始: {name_ja}")

        try:
            result = generate_v2_short(
                figure=figure_data,
                output_dir=output_dir,
                tts=tts,
                content_gen=content_gen,
                dry_run=args.dry_run,
            )

            elapsed = _time.time() - t0
            if result and page_id:
                notion.mark_v2_done(page_id, result)
                success += 1
                print(f"[{idx}/{total}] 完了 ({elapsed:.0f}秒): {name_ja}")
            elif args.dry_run:
                print(f"[{idx}/{total}] [DRY RUN] {name_ja}")
            else:
                print(f"[{idx}/{total}] 生成失敗: {name_ja}")
                failed.append(name_ja)
        except Exception as e:
            elapsed = _time.time() - t0
            logger.error(f"v2生成エラー ({name_ja}): {e}", exc_info=True)
            print(f"[{idx}/{total}] エラー ({elapsed:.0f}秒): {name_ja} - {e}")
            failed.append(name_ja)

        # 残り本数・推定残り時間
        if idx < total:
            avg = (_time.time() - batch_start) / idx
            remaining = avg * (total - idx)
            h, m = divmod(int(remaining), 3600)
            m, s = divmod(m, 60)
            eta = f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"
            print(f"  残り {total - idx} 件 / 推定残り時間: {eta}\n")

    total_elapsed = _time.time() - batch_start
    h, m = divmod(int(total_elapsed), 3600)
    m, s = divmod(m, 60)
    elapsed_str = f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"

    print(f"\n{'='*40}")
    print(f"完了: {success}/{total} 件 (所要時間: {elapsed_str})")
    if failed:
        print(f"失敗 ({len(failed)} 件):")
        for name in failed:
            print(f"  - {name}")


if __name__ == "__main__":
    main()
