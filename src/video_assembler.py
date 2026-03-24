"""
動画アセンブラモジュール
MoviePy + Pillow でYouTube Shorts動画を生成する

構成:
- 背景: Pexelsの縦型動画 or グラデーション画像
- テキスト: 字幕（上部: タイトル、中央: 字幕ループ）
- 音声: edge-ttsで生成した音声
"""
import os
import textwrap
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    VideoFileClip,
    concatenate_videoclips,
)

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config


# フォントパス（システムフォント or カスタム）
def _get_font_path(size: int = 60) -> str:
    """利用可能なフォントを探す"""
    candidates = [
        # Linux日本語フォント
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        # macOS
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        # assets内のフォント
        str(Path(__file__).parent.parent / "assets/fonts/NotoSansCJKjp-Bold.otf"),
        str(Path(__file__).parent.parent / "assets/fonts/font.ttf"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _create_gradient_background(
    width: int,
    height: int,
    color1: tuple = (15, 10, 60),
    color2: tuple = (80, 20, 120),
) -> np.ndarray:
    """グラデーション背景画像をnumpy arrayで生成"""
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        ratio = y / height
        r = int(color1[0] * (1 - ratio) + color2[0] * ratio)
        g = int(color1[1] * (1 - ratio) + color2[1] * ratio)
        b = int(color1[2] * (1 - ratio) + color2[2] * ratio)
        arr[y, :] = [r, g, b]
    return arr


def _render_text_image(
    text: str,
    width: int,
    height: int,
    font_size: int = 55,
    text_color: tuple = (255, 255, 255),
    bg_color: tuple | None = (0, 0, 0, 160),
    padding: int = 20,
    max_width_ratio: float = 0.88,
) -> Image.Image:
    """テキストをPillowで描画してPIL Imageを返す（透過対応）"""
    font_path = _get_font_path()
    try:
        if font_path:
            font = ImageFont.truetype(font_path, font_size)
        else:
            font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    # テキスト折り返し
    max_chars = max(1, int(max_width_ratio * width / (font_size * 0.6)))
    wrapped = textwrap.fill(text, width=max_chars)
    lines = wrapped.split("\n")

    # テキストサイズ計算
    dummy_img = Image.new("RGBA", (1, 1))
    dummy_draw = ImageDraw.Draw(dummy_img)
    line_heights = []
    line_widths = []
    for line in lines:
        bbox = dummy_draw.textbbox((0, 0), line, font=font)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])

    total_height = sum(line_heights) + (len(lines) - 1) * 10 + padding * 2
    max_line_width = max(line_widths) if line_widths else width

    img_w = min(width - 40, max_line_width + padding * 2)
    img_h = total_height

    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 背景矩形（半透明）
    if bg_color:
        draw.rounded_rectangle([0, 0, img_w - 1, img_h - 1], radius=15, fill=bg_color)

    # テキスト描画
    y = padding
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        x = (img_w - lw) // 2
        # シャドウ
        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0, 200))
        draw.text((x, y), line, font=font, fill=(*text_color, 255))
        y += line_heights[i] + 10

    return img


def create_short_video(
    audio_path: str,
    subtitle_segments: list,
    title: str,
    thumbnail_text: str,
    output_path: str,
    background_path: str = None,
    duration: float = None,
) -> str:
    """
    YouTube Shorts動画を生成する

    Args:
        audio_path: 音声ファイルパス (.mp3)
        subtitle_segments: [{"text": str, "start": float, "end": float}]
        title: 動画タイトル（上部表示）
        thumbnail_text: サムネイル用テキスト
        output_path: 出力動画パス (.mp4)
        background_path: 背景動画/画像パス (Noneの場合はグラデーション生成)
        duration: 動画の長さ（秒）、Noneなら音声の長さ

    Returns:
        出力ファイルパス
    """
    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    FPS = config.VIDEO_FPS

    # 音声読み込み
    audio_clip = AudioFileClip(audio_path)
    video_duration = duration or audio_clip.duration
    video_duration = min(video_duration, config.VIDEO_DURATION_MAX)

    # ==========================================
    # 背景レイヤー
    # ==========================================
    if background_path and os.path.exists(background_path):
        ext = Path(background_path).suffix.lower()
        if ext in [".mp4", ".mov", ".avi", ".mkv"]:
            bg_clip = VideoFileClip(background_path).without_audio()
            # ループして長さを合わせる
            if bg_clip.duration < video_duration:
                n_loops = int(video_duration / bg_clip.duration) + 1
                bg_clip = concatenate_videoclips([bg_clip] * n_loops)
            bg_clip = bg_clip.subclipped(0, video_duration)
            # リサイズ（縦型にクロップ）
            bg_clip = bg_clip.resized(height=H)
            if bg_clip.w < W:
                bg_clip = bg_clip.resized(width=W)
            # センタークロップ
            x_center = bg_clip.w // 2
            bg_clip = bg_clip.cropped(
                x1=x_center - W // 2,
                x2=x_center + W // 2,
                y1=0,
                y2=H,
            )
        else:
            # 画像の場合
            pil_img = Image.open(background_path).convert("RGB")
            # センタークロップで縦型に
            img_w, img_h = pil_img.size
            target_ratio = W / H
            curr_ratio = img_w / img_h
            if curr_ratio > target_ratio:
                new_w = int(img_h * target_ratio)
                left = (img_w - new_w) // 2
                pil_img = pil_img.crop((left, 0, left + new_w, img_h))
            else:
                new_h = int(img_w / target_ratio)
                top = (img_h - new_h) // 2
                pil_img = pil_img.crop((0, top, img_w, top + new_h))
            pil_img = pil_img.resize((W, H))
            # ぼかしを加えて文字が読みやすく
            pil_img = pil_img.filter(ImageFilter.GaussianBlur(radius=3))
            # 暗くする
            overlay = Image.new("RGB", (W, H), (0, 0, 0))
            bg_arr = np.array(Image.blend(pil_img, overlay, alpha=0.4))
            bg_clip = ImageClip(bg_arr).with_duration(video_duration)
    else:
        # グラデーション背景（フォールバック）
        grad_arr = _create_gradient_background(W, H)
        bg_clip = ImageClip(grad_arr).with_duration(video_duration)

    # ==========================================
    # タイトルバー（上部）
    # ==========================================
    title_img = _render_text_image(
        text=title,
        width=W,
        height=H,
        font_size=48,
        text_color=(255, 255, 100),
        bg_color=(0, 0, 0, 180),
        padding=16,
    )
    title_arr = np.array(title_img)
    title_clip = (
        ImageClip(title_arr)
        .with_duration(video_duration)
        .with_position(("center", 80))
    )

    # ==========================================
    # 字幕クリップ（音声に同期）
    # ==========================================
    subtitle_clips = []
    for seg in subtitle_segments:
        if seg["start"] >= video_duration:
            break
        seg_end = min(seg["end"], video_duration)
        seg_duration = seg_end - seg["start"]
        if seg_duration <= 0:
            continue

        sub_img = _render_text_image(
            text=seg["text"],
            width=W,
            height=H,
            font_size=58,
            text_color=(255, 255, 255),
            bg_color=(0, 0, 0, 200),
            padding=18,
        )
        sub_arr = np.array(sub_img)
        sub_clip = (
            ImageClip(sub_arr)
            .with_duration(seg_duration)
            .with_start(seg["start"])
            .with_position(("center", H - 400))
        )
        subtitle_clips.append(sub_clip)

    # ==========================================
    # サムネイル用大テキスト（中央、最初の2秒）
    # ==========================================
    thumb_img = _render_text_image(
        text=thumbnail_text,
        width=W,
        height=H,
        font_size=72,
        text_color=(255, 220, 50),
        bg_color=(0, 0, 0, 150),
        padding=24,
    )
    thumb_arr = np.array(thumb_img)
    thumb_clip = (
        ImageClip(thumb_arr)
        .with_duration(min(2.5, video_duration))
        .with_start(0)
        .with_position("center")
    )

    # ==========================================
    # 合成
    # ==========================================
    all_clips = [bg_clip, title_clip, thumb_clip] + subtitle_clips
    final = CompositeVideoClip(all_clips, size=(W, H))
    final = final.with_audio(audio_clip.subclipped(0, video_duration))

    # ==========================================
    # エクスポート
    # ==========================================
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    final.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="fast",
        threads=2,
        logger=None,  # ログを抑制
    )

    # クリーンアップ
    final.close()
    audio_clip.close()

    return output_path


if __name__ == "__main__":
    # テスト（ダミーデータで実行）
    print("動画アセンブラ - テスト実行")
    print("実際のテストはパイプライン経由で実行してください")
