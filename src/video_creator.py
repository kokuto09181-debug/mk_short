"""
動画生成モジュール
MoviePy + Pillow で YouTube Shorts (1080x1920, 9:16) 動画を生成する

レイアウト:
  上部 42% : Wikipedia 顔写真（ポートレート）
  下部 58% : タイトル・本文・字幕・CTA・プログレスバー
テキストはセクション切り替わり時にフェードイン
"""

import logging
import os
import random
import re
import textwrap
from pathlib import Path
from typing import Optional

import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"

JAPANESE_FONT_PATHS = [
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/truetype/vlgothic/VL-Gothic-Regular.ttf",
    "/usr/share/fonts/truetype/ipafont-gothic/ipagp.ttf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "C:/Windows/Fonts/meiryo.ttc",
]

# 顔写真エリアの高さ比率
PORTRAIT_RATIO = 0.42


def load_config() -> dict:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_japanese_font() -> Optional[str]:
    for path in JAPANESE_FONT_PATHS:
        if os.path.exists(path):
            return path
    # fc-list でインストール済みフォントを動的検索
    try:
        import subprocess
        result = subprocess.run(
            ["fc-list", ":lang=ja", "--format=%{file}\n"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            path = line.strip().split(":")[0]
            if path and os.path.exists(path):
                logger.info(f"fc-list でフォント発見: {path}")
                return path
    except Exception:
        pass
    logger.warning("日本語フォントが見つかりません。デフォルトフォントを使用します")
    return None


class VideoCreator:
    def __init__(self):
        self.config = load_config()
        self.video_config = self.config["video"]
        self.width = self.video_config["width"]
        self.height = self.video_config["height"]
        self.fps = self.video_config["fps"]
        self.font_path = find_japanese_font()

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont:
        if self.font_path:
            try:
                return ImageFont.truetype(self.font_path, size)
            except Exception:
                pass
        return ImageFont.load_default()

    def _pick_theme(self) -> dict:
        return random.choice(self.video_config["themes"])

    def _create_background_frame(
        self,
        bg_image_path: Optional[str],
        theme: dict,
        overlay_alpha: int = 190,
    ) -> Image.Image:
        """背景フレームを生成する（画像 or グラデーション）"""
        if bg_image_path and os.path.exists(bg_image_path):
            img = Image.open(bg_image_path).convert("RGB")
            img_ratio = img.width / img.height
            target_ratio = self.width / self.height
            if img_ratio > target_ratio:
                new_w = int(img.height * target_ratio)
                left = (img.width - new_w) // 2
                img = img.crop((left, 0, left + new_w, img.height))
            else:
                new_h = int(img.width / target_ratio)
                top = (img.height - new_h) // 2
                img = img.crop((0, top, img.width, top + new_h))
            img = img.resize((self.width, self.height), Image.LANCZOS)
            img = img.filter(ImageFilter.GaussianBlur(radius=5))

            overlay = Image.new("RGBA", img.size, (*theme["bg_color"], overlay_alpha))
            img = img.convert("RGBA")
            img = Image.alpha_composite(img, overlay).convert("RGB")
        else:
            arr = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            bg = theme["bg_color"]
            for y in range(self.height):
                ratio = y / self.height
                r = int(bg[0] * (1 - ratio * 0.5))
                g = int(bg[1] * (1 - ratio * 0.5))
                b = int(bg[2] * (1 - ratio * 0.5) + 30 * ratio)
                arr[y, :] = [r, g, b]
            img = Image.fromarray(arr, "RGB")

        return img

    def _paste_portrait(self, img: Image.Image, portrait_path: str, theme: dict) -> int:
        """
        顔写真を上部 PORTRAIT_RATIO に貼り付け、下端にグラデーションをかける。
        貼り付けた高さ（px）を返す。失敗時は 0 を返す。
        """
        portrait_h = int(self.height * PORTRAIT_RATIO)
        try:
            face = Image.open(portrait_path).convert("RGB")
        except Exception as e:
            logger.warning(f"顔写真読み込み失敗: {e}")
            return 0

        # センタークロップ
        target_ratio = self.width / portrait_h
        face_ratio = face.width / face.height
        if face_ratio > target_ratio:
            new_w = int(face.height * target_ratio)
            left = (face.width - new_w) // 2
            face = face.crop((left, 0, left + new_w, face.height))
        else:
            new_h = int(face.width / target_ratio)
            top = (face.height - new_h) // 2
            face = face.crop((0, top, face.width, top + new_h))
        face = face.resize((self.width, portrait_h), Image.LANCZOS)

        # 下部65%以降を背景色にフェード
        overlay_arr = np.zeros((portrait_h, self.width, 4), dtype=np.uint8)
        bg = theme["bg_color"]
        fade_start = int(portrait_h * 0.65)
        for y in range(portrait_h):
            if y >= fade_start:
                alpha = int((y - fade_start) / (portrait_h - fade_start) * 255)
                overlay_arr[y, :] = (*bg, alpha)

        face_blended = Image.alpha_composite(
            face.convert("RGBA"),
            Image.fromarray(overlay_arr, "RGBA"),
        ).convert("RGB")
        img.paste(face_blended, (0, 0))
        return portrait_h

    def _draw_text_with_shadow(
        self,
        draw: ImageDraw.Draw,
        text: str,
        position: tuple,
        font: ImageFont.FreeTypeFont,
        color: tuple,
        shadow_offset: int = 3,
        alpha: int = 255,
    ):
        """影付きテキストを描画する（alpha でフェードイン対応）"""
        x, y = position
        shadow_alpha = int(180 * alpha / 255)
        draw.text(
            (x + shadow_offset, y + shadow_offset),
            text, font=font, fill=(0, 0, 0, shadow_alpha),
        )
        draw.text((x, y), text, font=font, fill=(*color, alpha))

    def _wrap_text(self, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
        """テキストを折り返す"""
        lines = []
        for paragraph in text.split("\n"):
            if not paragraph:
                lines.append("")
                continue
            current_line = ""
            for char in list(paragraph):
                test_line = current_line + char
                bbox = font.getbbox(test_line)
                if bbox[2] > max_width and current_line:
                    lines.append(current_line)
                    current_line = char
                else:
                    current_line = test_line
            if current_line:
                lines.append(current_line)
        return lines

    def _split_sentences(self, text: str, language: str = "ja") -> list[str]:
        """ナレーションを文単位に分割する"""
        if language == "ja":
            parts = re.split(r'(?<=[。！？])', text)
        else:
            parts = re.split(r'(?<=[.!?])\s+', text)
        return [p.strip() for p in parts if p.strip()]

    def _make_subtitle_func(self, sentences: list[str], total_duration: float):
        """時刻 t を受け取り、その時点の字幕テキストを返すクロージャを生成する"""
        if not sentences:
            return lambda t: ""
        total_chars = sum(len(s) for s in sentences)
        if total_chars == 0:
            return lambda t: ""
        starts = []
        cumulative = 0
        for s in sentences:
            starts.append(cumulative / total_chars * total_duration)
            cumulative += len(s)

        def get_subtitle(t: float) -> str:
            current = sentences[0]
            for i, start in enumerate(starts):
                if t >= start:
                    current = sentences[i]
            return current

        return get_subtitle

    def create_frame(
        self,
        script: dict,
        section_index: int,
        portrait_path: Optional[str],
        bg_image_path: Optional[str],
        theme: dict,
        progress: float = 0.0,
        subtitle_text: str = "",
        section_progress: float = 1.0,
    ) -> np.ndarray:
        """1フレーム（PIL Image -> numpy array）を生成する"""
        img = self._create_background_frame(bg_image_path, theme)

        # --- 顔写真エリア（上部 PORTRAIT_RATIO）
        portrait_h = 0
        if portrait_path and os.path.exists(portrait_path):
            portrait_h = self._paste_portrait(img, portrait_path, theme)

        draw = ImageDraw.Draw(img, "RGBA")

        # テキストフェードイン（0.5秒でフル表示）
        text_alpha = int(min(1.0, section_progress / 0.5) * 255)

        padding = 60
        content_width = self.width - padding * 2
        accent_color = theme["accent_color"]
        title_color = theme["title_color"]
        text_color = theme["text_color"]

        # テキスト開始Y座標
        if portrait_h > 0:
            text_start = portrait_h + 20
            draw.rectangle(
                [(padding, text_start), (self.width - padding, text_start + 4)],
                fill=(*accent_color, text_alpha),
            )
            text_start += 24
        else:
            draw.rectangle([(0, 0), (self.width, 8)], fill=(*accent_color, 255))
            text_start = 80

        # --- タイトル
        title_font = self._get_font(self.video_config["font_size_title"])
        title_lines = self._wrap_text(script.get("title", ""), title_font, content_width)

        cur_y = text_start
        for line in title_lines[:2]:
            self._draw_text_with_shadow(
                draw, line, (padding, cur_y), title_font, title_color, alpha=text_alpha,
            )
            bbox = title_font.getbbox(line)
            cur_y += bbox[3] - bbox[1] + 12
        cur_y += 18

        # --- 本文（セクションまたはフック）
        sections = script.get("sections", [])
        body_font = self._get_font(self.video_config["font_size_body"])
        small_font = self._get_font(self.video_config["font_size_small"])

        if sections and section_index < len(sections):
            section = sections[section_index]
            if section.get("heading"):
                h_lines = self._wrap_text(section["heading"], body_font, content_width)
                for line in h_lines[:1]:
                    self._draw_text_with_shadow(
                        draw, line, (padding, cur_y), body_font, accent_color, alpha=text_alpha,
                    )
                    bbox = body_font.getbbox(line)
                    cur_y += bbox[3] - bbox[1] + 10
                cur_y += 8

            max_lines = 6 if portrait_h > 0 else 8
            content_lines = self._wrap_text(section["content"], small_font, content_width)
            for line in content_lines[:max_lines]:
                self._draw_text_with_shadow(
                    draw, line, (padding, cur_y), small_font, text_color, alpha=text_alpha,
                )
                bbox = small_font.getbbox(line)
                cur_y += bbox[3] - bbox[1] + 12

        elif section_index == 0:
            hook_lines = self._wrap_text(script.get("hook", ""), body_font, content_width)
            for line in hook_lines[:4]:
                self._draw_text_with_shadow(
                    draw, line, (padding, cur_y), body_font, text_color, alpha=text_alpha,
                )
                bbox = body_font.getbbox(line)
                cur_y += bbox[3] - bbox[1] + 15

        # --- 字幕（下部固定エリア）
        if subtitle_text:
            sub_font = self._get_font(self.video_config.get("font_size_subtitle", 42))
            sub_lines = self._wrap_text(subtitle_text, sub_font, content_width - 20)[:3]
            if sub_lines:
                line_h = sub_font.getbbox("あ")[3] + 10
                area_h = len(sub_lines) * line_h + 24
                sub_top = self.height - 320 - area_h
                draw.rectangle(
                    [(padding - 20, sub_top - 8),
                     (self.width - padding + 20, sub_top + area_h)],
                    fill=(0, 0, 0, 175),
                )
                sy = sub_top
                for line in sub_lines:
                    self._draw_text_with_shadow(
                        draw, line, (padding, sy), sub_font, (255, 255, 255),
                        shadow_offset=2, alpha=255,
                    )
                    sy += line_h

        # --- CTA
        cta_font = self._get_font(self.video_config["font_size_body"])
        cta_y = self.height - 250
        cta_text = script.get("cta", "")
        if cta_text:
            draw.rectangle(
                [(padding - 20, cta_y - 20),
                 (self.width - padding + 20, cta_y + 90)],
                fill=(*accent_color, 60),
            )
            draw.rectangle(
                [(padding - 20, cta_y - 20),
                 (self.width - padding + 20, cta_y - 16)],
                fill=(*accent_color, 255),
            )
            cta_lines = self._wrap_text(cta_text, cta_font, content_width)
            for line in cta_lines[:2]:
                self._draw_text_with_shadow(
                    draw, line, (padding, cta_y), cta_font, title_color, alpha=255,
                )
                bbox = cta_font.getbbox(line)
                cta_y += bbox[3] - bbox[1] + 10

        # --- プログレスバー
        bar_y = self.height - 12
        draw.rectangle([(0, bar_y), (self.width, self.height)], fill=(30, 30, 30, 255))
        draw.rectangle(
            [(0, bar_y), (int(self.width * progress), self.height)],
            fill=(*accent_color, 255),
        )

        return np.array(img)

    def _draw_subtitle_on_array(self, frame: np.ndarray, subtitle: str) -> np.ndarray:
        """字幕テキストをnumpy配列フレームに描画して返す"""
        img = Image.fromarray(frame)
        draw = ImageDraw.Draw(img, "RGBA")
        sub_font = self._get_font(self.video_config.get("font_size_subtitle", 42))
        padding = 60
        content_width = self.width - padding * 2
        sub_lines = self._wrap_text(subtitle, sub_font, content_width - 20)[:3]
        if sub_lines:
            line_h = sub_font.getbbox("あ")[3] + 10
            area_h = len(sub_lines) * line_h + 24
            sub_top = self.height - 320 - area_h
            draw.rectangle(
                [(padding - 20, sub_top - 8),
                 (self.width - padding + 20, sub_top + area_h)],
                fill=(0, 0, 0, 175),
            )
            sy = sub_top
            for line in sub_lines:
                self._draw_text_with_shadow(
                    draw, line, (padding, sy), sub_font, (255, 255, 255),
                    shadow_offset=2, alpha=255,
                )
                sy += line_h
        return np.array(img)

    def _overlay_end_card(self, frame: np.ndarray, video_id: str) -> np.ndarray:
        """末尾数秒に「続きはこちら！」エンドカードを描画する"""
        img = Image.fromarray(frame)
        rgba = img.convert("RGBA")
        overlay = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        card_h = 210
        card_top = self.height - card_h - 15
        draw.rectangle([(0, card_top), (self.width, self.height)], fill=(0, 0, 0, 210))

        font_large = self._get_font(64)
        text = "続きはこちら！"
        bbox = font_large.getbbox(text)
        x = (self.width - (bbox[2] - bbox[0])) // 2
        draw.text((x + 2, card_top + 20), text, font=font_large, fill=(0, 0, 0, 180))
        draw.text((x, card_top + 18), text, font=font_large, fill=(255, 215, 80, 255))

        font_small = self._get_font(44)
        url_text = f"▶ youtu.be/{video_id}"
        bbox2 = font_small.getbbox(url_text)
        ux = (self.width - (bbox2[2] - bbox2[0])) // 2
        draw.text((ux + 1, card_top + 105), url_text, font=font_small, fill=(0, 0, 0, 160))
        draw.text((ux, card_top + 103), url_text, font=font_small, fill=(180, 210, 255, 255))

        return np.array(Image.alpha_composite(rgba, overlay).convert("RGB"))

    def create_video(
        self,
        script: dict,
        audio_path: str,
        image_paths: list[str],
        output_path: str,
        narration: str = "",
        portrait_path: Optional[str] = None,
        longform_video_id: Optional[str] = None,
    ) -> str:
        """動画を生成して output_path に保存する"""
        from moviepy.editor import AudioFileClip, VideoClip

        logger.info(f"動画生成開始: {script.get('title', 'untitled')}")
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        theme = self._pick_theme()
        audio_clip = AudioFileClip(audio_path)
        total_duration = audio_clip.duration

        # 字幕タイミング関数
        language = script.get("language", "ja")
        sentences = self._split_sentences(narration, language) if narration else []
        get_subtitle = self._make_subtitle_func(sentences, total_duration)

        sections = script.get("sections", [])
        num_sections = max(len(sections), 1)
        section_duration = total_duration / num_sections

        # ─── プリレンダリング: セクションごとに基本フレームを1枚だけ描画 ───
        # (プログレスバー・字幕なし、フェードイン完了状態)
        logger.info(f"基本フレームをプリレンダリング中 ({num_sections}枚)...")
        base_frames: list[np.ndarray] = []
        for i in range(num_sections):
            bg = image_paths[i % len(image_paths)] if image_paths else None
            frame = self.create_frame(
                script, i, portrait_path, bg, theme,
                progress=0.0, subtitle_text="", section_progress=1.0,
            )
            base_frames.append(frame.copy())

        # ─── 字幕キャッシュ: (section_idx, subtitle) → 字幕描画済みarray ───
        subtitle_cache: dict[tuple, np.ndarray] = {}

        def get_subtitled_frame(section_idx: int, subtitle: str) -> np.ndarray:
            key = (section_idx, subtitle)
            if key not in subtitle_cache:
                if subtitle:
                    subtitle_cache[key] = self._draw_subtitle_on_array(
                        base_frames[section_idx], subtitle
                    )
                else:
                    subtitle_cache[key] = base_frames[section_idx]
            return subtitle_cache[key]

        accent = list(theme["accent_color"])
        bar_y = self.height - 12
        end_card_start = total_duration - 3.5

        def make_frame(t: float) -> np.ndarray:
            section_idx = min(int(t / section_duration), num_sections - 1)
            section_t = t - section_idx * section_duration
            section_progress = min(1.0, section_t / 0.5)
            progress = t / total_duration
            subtitle = get_subtitle(t)

            # フェードイン中（各セクション先頭0.5秒）はフル描画
            if section_progress < 1.0:
                bg = image_paths[section_idx % len(image_paths)] if image_paths else None
                frame = self.create_frame(
                    script, section_idx, portrait_path, bg, theme,
                    progress, subtitle, section_progress,
                )
            else:
                # フェードイン完了後: キャッシュ済みフレームをコピーしてプログレスバーのみ更新
                frame = get_subtitled_frame(section_idx, subtitle).copy()
                frame[bar_y:, :] = [30, 30, 30]
                pw = int(self.width * progress)
                if pw > 0:
                    frame[bar_y:, :pw] = accent

            # 末尾3.5秒: エンドカードを重ねる
            if longform_video_id and t >= end_card_start:
                frame = self._overlay_end_card(frame, longform_video_id)

            return frame

        video = VideoClip(make_frame=make_frame, duration=total_duration)
        video = video.set_audio(audio_clip)
        video = video.set_fps(self.fps)

        logger.info(f"動画書き出し: {output_path} ({total_duration:.1f}秒)")
        use_gpu = os.environ.get("USE_GPU_ENCODER", "").lower() in ("1", "true", "yes")
        if use_gpu:
            logger.info("エンコーダー: h264_nvenc (GPU)")
            video.write_videofile(
                output_path,
                fps=self.fps,
                codec="h264_nvenc",
                audio_codec="aac",
                ffmpeg_params=["-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0"],
                threads=8,
                logger=None,
            )
        else:
            logger.info("エンコーダー: libx264 (CPU)")
            video.write_videofile(
                output_path,
                fps=self.fps,
                codec="libx264",
                audio_codec="aac",
                preset="fast",
                threads=4,
                logger=None,
            )

        audio_clip.close()
        video.close()
        logger.info(f"動画生成完了: {output_path}")
        return output_path

    def create_thumbnail(self, script: dict, output_path: str) -> str:
        """YouTube サムネイル（1280x720）を生成する"""
        tw, th = 1280, 720
        theme = self._pick_theme()

        arr = np.zeros((th, tw, 3), dtype=np.uint8)
        bg = theme["bg_color"]
        for y in range(th):
            ratio = y / th
            r = min(255, int(bg[0] + 80 * (1 - ratio)))
            g = min(255, int(bg[1] + 40 * (1 - ratio)))
            b = min(255, int(bg[2] + 60 * (1 - ratio)))
            arr[y, :] = [r, g, b]
        img = Image.fromarray(arr, "RGB")
        draw = ImageDraw.Draw(img, "RGBA")

        padding = 60
        content_width = tw - padding * 2

        draw.rectangle([(0, 0), (tw, 12)], fill=(*theme["accent_color"], 255))
        draw.rectangle([(0, th - 12), (tw, th)], fill=(*theme["accent_color"], 255))

        thumb_text = script.get("thumbnail_text") or script.get("title", "")
        font_large = self._get_font(110)
        font_small = self._get_font(54)

        lines = self._wrap_text(thumb_text, font_large, content_width)
        y = 160
        for line in lines[:3]:
            self._draw_text_with_shadow(
                draw, line, (padding, y), font_large, theme["title_color"], shadow_offset=5,
            )
            bbox = font_large.getbbox(line)
            y += bbox[3] - bbox[1] + 20

        title_lines = self._wrap_text(script.get("title", ""), font_small, content_width)
        ty = th - 180
        for line in title_lines[:2]:
            self._draw_text_with_shadow(
                draw, line, (padding, ty), font_small, theme["text_color"],
            )
            bbox = font_small.getbbox(line)
            ty += bbox[3] - bbox[1] + 10

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        img.save(output_path, "JPEG", quality=90)
        logger.info(f"サムネイル生成: {output_path}")
        return output_path
