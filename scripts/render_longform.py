"""
長編動画レンダリングスクリプト
Notionから long_script_ja（longform_status=script_ready）を読み込み、
1920×1080 横型動画をローカルに生成して保存する。

レイアウト:
  - 背景: Pexels 横型画像（全面・ぼかし＋暗色オーバーレイ）
  - 人物画像: 中央大きくオーバーレイ
  - セクション見出し: 左上
  - リアルタイム字幕: 下部

使用方法:
  python scripts/render_longform.py           # 未レンダリングを全件処理
  python scripts/render_longform.py --limit 1 # 最大N件処理
"""

import argparse
import logging
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pydub import AudioSegment

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from notion_client import NotionFigureClient
from tts_generator import TTSGenerator
from image_fetcher import ImageFetcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

W, H = 1920, 1080
FPS = 30
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "longform_output"
PEXELS_API_BASE = "https://api.pexels.com/v1"

JAPANESE_FONT_PATHS = [
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/truetype/vlgothic/VL-Gothic-Regular.ttf",
    "/usr/share/fonts/truetype/ipafont-gothic/ipagp.ttf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "C:/Windows/Fonts/meiryo.ttc",
]


def find_font() -> Optional[str]:
    for path in JAPANESE_FONT_PATHS:
        if os.path.exists(path):
            return path
    return None


def safe_dirname(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\s]', '_', name)


# ─────────────────────────────────────────
# スクリプトパーサー
# ─────────────────────────────────────────

def parse_script(text: str) -> dict:
    """long_script_ja プレーンテキストをパースして構造化データに変換する"""
    chunks = text.split("==============================")
    result = {"title": "", "description": "", "tags": "", "sections": []}

    header_text = chunks[0].strip() if chunks else ""
    for line in header_text.split("\n"):
        line = line.strip()
        if line.startswith("タイトル:"):
            result["title"] = line.split(":", 1)[1].strip()
        elif line.startswith("説明文:"):
            result["description"] = line.split(":", 1)[1].strip()
        elif line.startswith("タグ:"):
            result["tags"] = line.split(":", 1)[1].strip()

    for chunk in chunks[1:]:
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = [l.strip() for l in chunk.split("\n") if l.strip()]
        if not lines:
            continue
        heading = lines[0]
        narration = "\n".join(lines[1:]).strip()
        if narration:
            result["sections"].append({"heading": heading, "narration": narration})

    return result


# ─────────────────────────────────────────
# 画像取得
# ─────────────────────────────────────────

def fetch_pexels_landscape(
    keywords: list, api_key: str, output_dir: str, count: int = 8
) -> list:
    """Pexelsから横型(landscape)画像を取得してダウンロードし、パスリストを返す"""
    os.makedirs(output_dir, exist_ok=True)
    session = requests.Session()
    session.headers.update({"Authorization": api_key})

    query = " ".join(str(k) for k in keywords[:2] if k)
    params = {
        "query": query or "Japan landscape",
        "orientation": "landscape",
        "per_page": count + 4,
        "size": "large",
    }
    try:
        resp = session.get(f"{PEXELS_API_BASE}/search", params=params, timeout=15)
        resp.raise_for_status()
        photos = resp.json().get("photos", [])
    except Exception as e:
        logger.warning(f"Pexels検索失敗 ({query}): {e}")
        photos = []

    if not photos:
        params["query"] = "Japan historical landscape"
        try:
            resp = session.get(f"{PEXELS_API_BASE}/search", params=params, timeout=15)
            resp.raise_for_status()
            photos = resp.json().get("photos", [])
        except Exception:
            return []

    paths = []
    for i, photo in enumerate(photos[:count]):
        url = photo["src"].get("large2x") or photo["src"]["large"]
        ext = url.split("?")[0].rsplit(".", 1)[-1] or "jpg"
        out_path = os.path.join(output_dir, f"bg_{i:02d}.{ext}")
        try:
            r = session.get(url, timeout=30, stream=True)
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            paths.append(out_path)
        except Exception as e:
            logger.warning(f"Pexels画像ダウンロード失敗: {e}")

    logger.info(f"Pexels landscape: {len(paths)}枚")
    return paths


# ─────────────────────────────────────────
# TTS生成
# ─────────────────────────────────────────

def generate_section_audios(
    sections: list, output_dir: str, tts_gen: TTSGenerator
) -> list:
    """各セクションのナレーションTTSを生成し、パスリスト（失敗はNone）を返す"""
    paths = []
    for i, section in enumerate(sections):
        narration = section.get("narration", "").strip()
        if not narration:
            paths.append(None)
            continue
        audio_path = os.path.join(output_dir, f"section_{i:02d}.mp3")
        try:
            tts_gen.generate(narration, audio_path)
            paths.append(audio_path)
            logger.info(f"  TTS [{i+1}/{len(sections)}]: {len(narration)}文字")
        except Exception as e:
            logger.error(f"  TTS失敗 [{i+1}]: {e}")
            paths.append(None)
        time.sleep(0.5)
    return paths


# ─────────────────────────────────────────
# フレーム生成
# ─────────────────────────────────────────

class LongformRenderer:
    """1920×1080 横型長編動画のフレーム生成・合成"""

    PORTRAIT_MAX_H = 580
    PORTRAIT_MAX_W = 520

    def __init__(self):
        self.font_path = find_font()

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont:
        if self.font_path:
            try:
                return ImageFont.truetype(self.font_path, size)
            except Exception:
                pass
        return ImageFont.load_default()

    def _make_bg_image(self, bg_path: Optional[str]) -> Image.Image:
        """1920×1080 背景（Pexels画像 + ぼかし + 暗色オーバーレイ）"""
        if bg_path and os.path.exists(bg_path):
            img = Image.open(bg_path).convert("RGB")
            img_r = img.width / img.height
            tgt_r = W / H
            if img_r > tgt_r:
                nw = int(img.height * tgt_r)
                left = (img.width - nw) // 2
                img = img.crop((left, 0, left + nw, img.height))
            else:
                nh = int(img.width / tgt_r)
                top = (img.height - nh) // 2
                img = img.crop((0, top, img.width, top + nh))
            img = img.resize((W, H), Image.LANCZOS)
            img = img.filter(ImageFilter.GaussianBlur(radius=4))
            overlay = Image.new("RGBA", (W, H), (10, 10, 20, 165))
            img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        else:
            arr = np.zeros((H, W, 3), dtype=np.uint8)
            for y in range(H):
                r = y / H
                arr[y, :] = [int(15 + 10 * r), int(15 + 10 * r), int(25 + 20 * r)]
            img = Image.fromarray(arr, "RGB")
        return img

    def _overlay_portrait(
        self, img: Image.Image, portrait_path: Optional[str]
    ) -> Image.Image:
        """中央に人物画像をオーバーレイ（縦横比維持・最大サイズ制限）"""
        if not portrait_path or not os.path.exists(portrait_path):
            return img
        try:
            face = Image.open(portrait_path).convert("RGBA")
        except Exception as e:
            logger.warning(f"人物画像読み込み失敗: {e}")
            return img

        w, h = face.size
        scale = min(self.PORTRAIT_MAX_H / h, self.PORTRAIT_MAX_W / w, 1.0)
        new_w, new_h = int(w * scale), int(h * scale)
        face = face.resize((new_w, new_h), Image.LANCZOS)

        x = (W - new_w) // 2
        y = (H - new_h) // 2 - 20  # 少し上寄り

        result = img.convert("RGBA")
        result.paste(face, (x, y), face)
        return result.convert("RGB")

    def _draw_heading(self, img: Image.Image, heading: str) -> Image.Image:
        """左上にセクション見出しを描画（半透明背景付き）"""
        if not heading:
            return img
        rgba = img.convert("RGBA")
        overlay = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        font = self._get_font(52)
        x, y, pad = 40, 28, 16
        bbox = font.getbbox(heading)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        draw.rectangle(
            [(x - pad, y - pad // 2), (x + text_w + pad, y + text_h + pad // 2)],
            fill=(0, 0, 0, 170),
        )
        draw.text((x + 2, y + 2), heading, font=font, fill=(0, 0, 0, 150))  # 影
        draw.text((x, y), heading, font=font, fill=(255, 215, 80, 255))      # 金色

        return Image.alpha_composite(rgba, overlay).convert("RGB")

    def _wrap_text(self, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list:
        lines = []
        for para in text.split("\n"):
            if not para:
                continue
            current = ""
            for char in para:
                test = current + char
                if font.getbbox(test)[2] > max_w and current:
                    lines.append(current)
                    current = char
                else:
                    current = test
            if current:
                lines.append(current)
        return lines

    def _draw_subtitles(self, img: Image.Image, text: str) -> Image.Image:
        """下部に字幕を描画（半透明背景付き）"""
        if not text:
            return img
        rgba = img.convert("RGBA")
        overlay = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        font = self._get_font(50)
        pad_x = 80
        lines = self._wrap_text(text, font, W - pad_x * 2)[:3]
        if not lines:
            return img

        line_h = font.getbbox("あ")[3] + 14
        area_h = len(lines) * line_h + 20
        area_top = H - area_h - 50

        draw.rectangle(
            [(pad_x - 24, area_top - 10), (W - pad_x + 24, area_top + area_h)],
            fill=(0, 0, 0, 175),
        )
        sy = area_top + 8
        for line in lines:
            draw.text((pad_x + 2, sy + 2), line, font=font, fill=(0, 0, 0, 160))
            draw.text((pad_x, sy), line, font=font, fill=(255, 255, 255, 255))
            sy += line_h

        return Image.alpha_composite(rgba, overlay).convert("RGB")

    def _make_subtitle_func(self, narration: str, duration: float):
        """時刻 t を受け取り字幕テキストを返す関数を生成する"""
        sentences = [s for s in re.split(r'(?<=[。！？\n])', narration) if s.strip()]
        if not sentences:
            return lambda t: ""
        total_chars = sum(len(s) for s in sentences)
        if total_chars == 0:
            return lambda t: ""
        starts = []
        cumulative = 0
        for s in sentences:
            starts.append(cumulative / total_chars * duration)
            cumulative += len(s)

        def get_sub(t: float) -> str:
            current = sentences[0]
            for i, start in enumerate(starts):
                if t >= start:
                    current = sentences[i]
            return current

        return get_sub

    # ─────────────────────────────────────────
    # メイン動画レンダリング
    # ─────────────────────────────────────────

    def render_video(
        self,
        parsed: dict,
        section_audio_paths: list,
        bg_paths: list,
        portrait_path: Optional[str],
        output_path: str,
    ) -> str:
        """全セクションを結合して最終動画（BGM込み）を生成する"""
        from moviepy.editor import AudioFileClip, VideoClip, concatenate_videoclips

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        sections = parsed.get("sections", [])
        if not sections:
            raise ValueError("セクションが0件")

        clips = []
        for i, section in enumerate(sections):
            audio_path = section_audio_paths[i] if i < len(section_audio_paths) else None
            if not audio_path or not os.path.exists(audio_path):
                logger.warning(f"  セクション {i+1}: TTSなし、スキップ")
                continue

            audio_clip = AudioFileClip(audio_path)
            duration = audio_clip.duration

            bg_path = bg_paths[i % len(bg_paths)] if bg_paths else None
            heading = section.get("heading", "")
            narration = section.get("narration", "")

            # 基本フレームをプリレンダリング（字幕なし）
            base_img = self._make_bg_image(bg_path)
            base_img = self._overlay_portrait(base_img, portrait_path)
            base_img = self._draw_heading(base_img, heading)
            base_arr = np.array(base_img)

            get_subtitle = self._make_subtitle_func(narration, duration)
            subtitle_cache = {}

            def make_frame(
                t,
                _base=base_arr,
                _get_sub=get_subtitle,
                _cache=subtitle_cache,
                _self=self,
            ):
                subtitle = _get_sub(t)
                if subtitle not in _cache:
                    img = Image.fromarray(_base.copy())
                    img = _self._draw_subtitles(img, subtitle)
                    _cache[subtitle] = np.array(img)
                return _cache[subtitle]

            clip = VideoClip(make_frame=make_frame, duration=duration)
            clip.size = (W, H)  # MoviePy がサイズを知るよう明示
            clip = clip.set_audio(audio_clip)
            clip = clip.set_fps(FPS)
            clips.append(clip)
            logger.info(f"  セクション {i+1}/{len(sections)}: {heading[:30]} ({duration:.1f}秒)")

        if not clips:
            raise ValueError("有効なセクションがありません")

        logger.info(f"{len(clips)}セクション結合中...")
        # method="chain" で単純連結（"compose" は緑背景になる場合がある）
        final = concatenate_videoclips(clips, method="chain")
        total_duration = final.duration

        # BGMなし版を一時書き出し
        nobgm_path = output_path.replace(".mp4", "_nobgm.mp4")
        use_gpu = os.environ.get("USE_GPU_ENCODER", "").lower() in ("1", "true", "yes")
        write_kw = dict(fps=FPS, audio_codec="aac", threads=4, logger=None)

        logger.info(f"動画書き出し中... ({total_duration:.1f}秒、{total_duration/60:.1f}分)")
        if use_gpu:
            try:
                final.write_videofile(
                    nobgm_path, codec="h264_nvenc",
                    ffmpeg_params=["-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0"],
                    **write_kw,
                )
            except Exception as gpu_err:
                logger.warning(f"h264_nvenc失敗 ({gpu_err.__class__.__name__})、libx264にフォールバック")
                import shutil
                if os.path.exists(nobgm_path):
                    os.remove(nobgm_path)
                final.write_videofile(nobgm_path, codec="libx264", preset="fast", **write_kw)
        else:
            final.write_videofile(nobgm_path, codec="libx264", preset="fast", **write_kw)

        for c in clips:
            c.close()
        final.close()

        # BGM混合
        logger.info("BGM混合中...")
        tts_gen = TTSGenerator()

        # 動画から音声を抽出
        narration_wav = output_path + "_narration.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", nobgm_path, "-vn", "-acodec", "pcm_s16le", narration_wav],
            check=True, capture_output=True,
        )

        from pydub import AudioSegment as _AS
        narration_audio = _AS.from_file(narration_wav)

        # data/bgm/longform/ にMP3があればそちらを使用、なければ合成BGM
        bgm_dir = Path(__file__).parent.parent / "data" / "bgm" / "longform"
        bgm_files = list(bgm_dir.glob("*.mp3")) + list(bgm_dir.glob("*.ogg")) if bgm_dir.exists() else []

        if bgm_files:
            bgm_file = random.choice(bgm_files)
            logger.info(f"BGMファイル使用: {bgm_file.name}")
            bgm_raw = _AS.from_file(str(bgm_file))

            # サンプルレート・チャンネル数をナレーションに合わせる（不一致によるノイズ防止）
            bgm_raw = bgm_raw.set_frame_rate(narration_audio.frame_rate)
            bgm_raw = bgm_raw.set_channels(narration_audio.channels)
            bgm_raw = bgm_raw.set_sample_width(narration_audio.sample_width)

            # 冒頭の無音区間を除去（BGMファイルの先頭無音が「切れた」に見えるのを防ぐ）
            from pydub.silence import detect_leading_silence
            leading_ms = detect_leading_silence(bgm_raw, silence_threshold=-50)
            if leading_ms > 0:
                bgm_raw = bgm_raw[leading_ms:]
                logger.info(f"BGM冒頭の無音 {leading_ms}ms を除去")

            # 動画の長さに合わせてループ or クリップ
            narration_ms = len(narration_audio)
            if len(bgm_raw) < narration_ms:
                loops = (narration_ms // len(bgm_raw)) + 1
                bgm_raw = bgm_raw * loops
            bgm = (bgm_raw + (-14.0))[:narration_ms]
        else:
            logger.info("BGMファイルなし → 合成BGMを使用 (data/bgm/longform/ にMP3を置くと差し替え可能)")
            bgm_seg = tts_gen._generate_ambient_bgm(len(narration_audio) / 1000.0 + 2.0)
            bgm = (bgm_seg + (-14.0))[:len(narration_audio)]

        mixed = narration_audio.overlay(bgm)
        mixed_path = output_path + "_mixed.mp3"
        mixed.export(mixed_path, format="mp3", bitrate="192k")

        # 混合音声を動画に結合
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", nobgm_path, "-i", mixed_path,
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                output_path,
            ],
            check=True, capture_output=True,
        )
        os.remove(narration_wav)
        os.remove(mixed_path)
        os.remove(nobgm_path)

        logger.info(f"完成: {output_path}")
        return output_path


# ─────────────────────────────────────────
# メイン処理
# ─────────────────────────────────────────

def run(limit: int = 5, name: str = ""):
    notion = NotionFigureClient()
    notion.ensure_longform_properties()
    tts = TTSGenerator()
    img_fetcher = ImageFetcher()
    renderer = LongformRenderer()

    fetch_limit = 100 if name else limit
    figures = notion.get_figures_ready_for_longform_render(limit=fetch_limit)
    if name:
        figures = [f for f in figures if f.get("name_ja") == name]
    if not figures:
        logger.info("レンダリング対象なし。完了。")
        return

    success = 0
    for figure in figures:
        name_ja = figure.get("name_ja", "不明")
        page_id = figure["page_id"]

        work_dir = OUTPUT_DIR / safe_dirname(name_ja)
        output_path_check = work_dir / "output.mp4"

        # ローカルにすでに動画ファイルがあればスキップ（再レンダリング防止）
        if output_path_check.exists():
            logger.info(f"スキップ（ローカルに動画あり）: {name_ja} → {output_path_check}")
            notion.mark_longform_render_done(page_id)
            success += 1
            continue

        logger.info(f"レンダリング開始: {name_ja}")
        notion.mark_longform_rendering(page_id)
        img_dir = work_dir / "images"
        bg_dir = work_dir / "bg"
        audio_dir = work_dir / "audio"
        for d in [work_dir, img_dir, bg_dir, audio_dir]:
            d.mkdir(parents=True, exist_ok=True)

        try:
            long_script = figure.get("long_script_ja", "")
            if not long_script:
                raise ValueError("long_script_ja が空")

            parsed = parse_script(long_script)
            if not parsed.get("sections"):
                raise ValueError("セクションが0件（スクリプトのパース失敗）")

            logger.info(
                f"  タイトル: {parsed.get('title', '(なし)')}"
                f"  セクション数: {len(parsed['sections'])}"
            )

            # Wikipedia 人物画像取得
            portrait_paths = img_fetcher.fetch_wikipedia_images(
                name_ja, figure.get("name_en", ""), str(img_dir)
            )
            portrait_path = portrait_paths[0] if portrait_paths else None

            # Pexels 背景画像取得（landscape）
            tags = parsed.get("tags", "")
            keywords = [name_ja] + [t.strip() for t in tags.split(",") if t.strip()][:2]
            bg_paths = fetch_pexels_landscape(
                keywords,
                os.environ["PEXELS_API_KEY"],
                str(bg_dir),
                count=len(parsed["sections"]),
            )

            # セクションごとにTTS生成
            section_audio_paths = generate_section_audios(
                parsed["sections"], str(audio_dir), tts
            )
            if not any(p for p in section_audio_paths):
                raise ValueError("全セクションのTTS生成に失敗")

            # 動画レンダリング
            output_path = str(work_dir / "output.mp4")
            renderer.render_video(
                parsed, section_audio_paths, bg_paths, portrait_path, output_path
            )

            notion.mark_longform_render_done(page_id)
            success += 1
            logger.info(f"完了: {name_ja} → {output_path}")

        except Exception as e:
            logger.error(f"エラー: {name_ja}: {e}", exc_info=True)
            notion.mark_longform_render_error(page_id, str(e))

        time.sleep(2)

    logger.info(f"=== 完了: {success}/{len(figures)} 件 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="長編動画ローカルレンダリング")
    parser.add_argument("--limit", type=int, default=5, help="最大処理件数（デフォルト: 5）")
    parser.add_argument("--name", type=str, default="", help="特定の偉人名（日本語）を指定して1件のみ処理")
    args = parser.parse_args()
    run(limit=args.limit, name=args.name)
