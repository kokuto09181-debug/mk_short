"""
動画生成モジュール
MoviePy + Pillow で YouTube Shorts (1080x1920, 9:16) 動画を生成する
"""

import logging
import os
import random
import textwrap
from pathlib import Path
from typing import Optional

import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"

# システムの日本語フォントパス候補
JAPANESE_FONT_PATHS = [
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/truetype/vlgothic/VL-Gothic-Regular.ttf",
    "/usr/share/fonts/truetype/ipafont-gothic/ipagp.ttf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "C:/Windows/Fonts/meiryo.ttc",
]


def load_config() -> dict:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_japanese_font() -> Optional[str]:
    """利用可能な日本語フォントパスを返す"""
    for path in JAPANESE_FONT_PATHS:
        if os.path.exists(path):
            return path
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
        themes = self.video_config["themes"]
        return random.choice(themes)

    def _create_background_frame(
        self,
        bg_image_path: Optional[str],
        theme: dict,
        overlay_alpha: int = 170,
    ) -> Image.Image:
        """背景フレームを生成する（画像 or グラデーション）"""
        if bg_image_path and os.path.exists(bg_image_path):
            img = Image.open(bg_image_path).convert("RGB")
            # Shortsサイズにクロップ（センタークロップ）
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
            img = img.filter(ImageFilter.GaussianBlur(radius=3))

            # ダーク オーバーレイを乗せる
            overlay = Image.new("RGBA", img.size, (*theme["bg_color"], overlay_alpha))
            img = img.convert("RGBA")
            img = Image.alpha_composite(img, overlay).convert("RGB")
        else:
            # グラデーション背景
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

    def _draw_text_with_shadow(
        self,
        draw: ImageDraw.Draw,
        text: str,
        position: tuple,
        font: ImageFont.FreeTypeFont,
        color: tuple,
        shadow_offset: int = 3,
    ):
        """影付きテキストを描画する"""
        x, y = position
        # 影
        draw.text((x + shadow_offset, y + shadow_offset), text, font=font, fill=(0, 0, 0, 180))
        # 本文
        draw.text((x, y), text, font=font, fill=(*color, 255))

    def _wrap_text(self, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
        """テキストを折り返す"""
        lines = []
        for paragraph in text.split("\n"):
            if not paragraph:
                lines.append("")
                continue
            words = list(paragraph)  # 日本語は1文字単位
            current_line = ""
            for char in words:
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

    def create_frame(
        self,
        script: dict,
        section_index: int,
        bg_image_path: Optional[str],
        theme: dict,
        progress: float = 0.0,
    ) -> np.ndarray:
        """1フレーム（PIL Image -> numpy array）を生成する"""
        img = self._create_background_frame(bg_image_path, theme)
        draw = ImageDraw.Draw(img, "RGBA")

        padding = 60
        content_width = self.width - padding * 2
        accent_color = theme["accent_color"]
        title_color = theme["title_color"]
        text_color = theme["text_color"]

        # --- アクセントライン（上部）
        draw.rectangle(
            [(0, 0), (self.width, 8)],
            fill=(*accent_color, 255),
        )

        # --- タイトル
        title_font = self._get_font(self.video_config["font_size_title"])
        title_text = script.get("title", "")
        title_lines = self._wrap_text(title_text, title_font, content_width)

        title_y = 120
        for line in title_lines[:2]:  # 最大2行
            self._draw_text_with_shadow(
                draw, line, (padding, title_y), title_font, title_color
            )
            bbox = title_font.getbbox(line)
            title_y += bbox[3] - bbox[1] + 15

        # --- 区切りライン
        title_y += 20
        draw.rectangle(
            [(padding, title_y), (self.width - padding, title_y + 4)],
            fill=(*accent_color, 200),
        )
        title_y += 30

        # --- 本文テキスト
        sections = script.get("sections", [])
        body_font = self._get_font(self.video_config["font_size_body"])
        small_font = self._get_font(self.video_config["font_size_small"])

        if sections and section_index < len(sections):
            section = sections[section_index]
            if section.get("heading"):
                heading_lines = self._wrap_text(section["heading"], body_font, content_width)
                for line in heading_lines[:1]:
                    self._draw_text_with_shadow(
                        draw, line, (padding, title_y), body_font, accent_color
                    )
                    bbox = body_font.getbbox(line)
                    title_y += bbox[3] - bbox[1] + 10
                title_y += 10

            content_lines = self._wrap_text(section["content"], small_font, content_width)
            for line in content_lines[:8]:  # 最大8行
                self._draw_text_with_shadow(
                    draw, line, (padding, title_y), small_font, text_color
                )
                bbox = small_font.getbbox(line)
                title_y += bbox[3] - bbox[1] + 12

        # --- フック（最初のセクション前）
        elif section_index == 0:
            hook_lines = self._wrap_text(script.get("hook", ""), body_font, content_width)
            for line in hook_lines[:4]:
                self._draw_text_with_shadow(
                    draw, line, (padding, title_y), body_font, text_color
                )
                bbox = body_font.getbbox(line)
                title_y += bbox[3] - bbox[1] + 15

        # --- CTA（最後）
        cta_font = self._get_font(self.video_config["font_size_body"])
        cta_y = self.height - 250
        cta_text = script.get("cta", "")
        if cta_text:
            # CTA背景
            draw.rectangle(
                [(padding - 20, cta_y - 20), (self.width - padding + 20, cta_y + 90)],
                fill=(*accent_color, 60),
            )
            draw.rectangle(
                [(padding - 20, cta_y - 20), (self.width - padding + 20, cta_y - 16)],
                fill=(*accent_color, 255),
            )
            cta_lines = self._wrap_text(cta_text, cta_font, content_width)
            for line in cta_lines[:2]:
                self._draw_text_with_shadow(
                    draw, line, (padding, cta_y), cta_font, title_color
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

    def create_video(
        self,
        script: dict,
        audio_path: str,
        image_paths: list[str],
        output_path: str,
    ) -> str:
        """動画を生成して output_path に保存する"""
        from moviepy.editor import (
            AudioFileClip,
            CompositeVideoClip,
            VideoClip,
            concatenate_videoclips,
        )

        logger.info(f"動画生成開始: {script.get('title', 'untitled')}")
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        theme = self._pick_theme()
        audio_clip = AudioFileClip(audio_path)
        total_duration = audio_clip.duration

        sections = script.get("sections", [])
        num_sections = max(len(sections), 1)

        # 各セクションの表示時間を計算
        section_duration = total_duration / num_sections
        clips = []

        for i in range(num_sections):
            start_t = i * section_duration
            end_t = (i + 1) * section_duration
            bg_image = image_paths[i % len(image_paths)] if image_paths else None

            def make_frame(t, idx=i, bg=bg_image):
                global_t = start_t + t
                progress = global_t / total_duration
                return self.create_frame(script, idx, bg, theme, progress)

            clip = VideoClip(
                make_frame=make_frame,
                duration=section_duration,
            )
            clips.append(clip)

        video = concatenate_videoclips(clips, method="compose")
        video = video.set_audio(audio_clip)
        video = video.set_fps(self.fps)

        logger.info(f"動画書き出し: {output_path} ({total_duration:.1f}秒)")
        video.write_videofile(
            output_path,
            fps=self.fps,
            codec="libx264",
            audio_codec="aac",
            preset="fast",
            threads=2,
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

        img = Image.new("RGB", (tw, th), tuple(theme["bg_color"]))
        draw = ImageDraw.Draw(img, "RGBA")

        # グラデーション背景
        arr = np.array(img)
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

        # アクセントライン
        draw.rectangle([(0, 0), (tw, 12)], fill=(*theme["accent_color"], 255))
        draw.rectangle([(0, th - 12), (tw, th)], fill=(*theme["accent_color"], 255))

        # サムネイルテキスト
        thumb_text = script.get("thumbnail_text") or script.get("title", "")
        font_large = self._get_font(110)
        font_small = self._get_font(54)

        lines = self._wrap_text(thumb_text, font_large, content_width)
        y = 160
        for line in lines[:3]:
            self._draw_text_with_shadow(
                draw, line, (padding, y), font_large, theme["title_color"], shadow_offset=5
            )
            bbox = font_large.getbbox(line)
            y += bbox[3] - bbox[1] + 20

        # タイトル（小）
        title = script.get("title", "")
        title_lines = self._wrap_text(title, font_small, content_width)
        ty = th - 180
        for line in title_lines[:2]:
            self._draw_text_with_shadow(
                draw, line, (padding, ty), font_small, theme["text_color"]
            )
            bbox = font_small.getbbox(line)
            ty += bbox[3] - bbox[1] + 10

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        img.save(output_path, "JPEG", quality=90)
        logger.info(f"サムネイル生成: {output_path}")
        return output_path
