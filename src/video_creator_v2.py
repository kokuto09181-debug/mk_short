"""
動画生成モジュール v2
Ken Burns効果（ズーム・パン） + テロップアニメーション + 効果音を追加。
既存の video_creator.py を置き換えず、新パイプライン用として独立動作する。
"""

import logging
import os
from pathlib import Path
from typing import Optional

# Pillow 10+ で ANTIALIAS が削除されたため moviepy 1.x 向けに互換パッチ
try:
    from PIL import Image as _PILImage
    if not hasattr(_PILImage, "ANTIALIAS"):
        _PILImage.ANTIALIAS = _PILImage.LANCZOS
except Exception:
    pass

import numpy as np
from moviepy.editor import (
    AudioFileClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    VideoClip,
    concatenate_videoclips,
)
from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
FONT_DIR = Path(__file__).parent.parent / "fonts"

# デフォルトフォント（Windows / Linux 互換）
_DEFAULT_FONTS = [
    FONT_DIR / "NotoSansJP-Bold.ttf",
    Path("C:/Windows/Fonts/YuGothB.ttc"),
    Path("C:/Windows/Fonts/meiryo.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
]


def _find_font(size: int = 42) -> ImageFont.FreeTypeFont:
    for p in _DEFAULT_FONTS:
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


# ─────────────────────────────────────────
# Ken Burns 効果
# ─────────────────────────────────────────

def ken_burns_clip(
    image_path: str,
    duration: float,
    target_size: tuple[int, int] = (1080, 1920),
    effect: str = "zoom_in",
    zoom_range: tuple[float, float] = (1.0, 1.15),
    fps: int = 30,
) -> VideoClip:
    """
    静止画にKen Burns効果（ズーム・パン）を適用したVideoClipを返す。

    effect:
      - "zoom_in":  ゆっくりズームイン
      - "zoom_out": ゆっくりズームアウト
      - "pan_left": 左へパン
      - "pan_right": 右へパン
      - "pan_up":   上へパン
    """
    w, h = target_size
    img = Image.open(image_path).convert("RGB")

    # 画像を少し大きめにリサイズ（ズーム余白確保）
    max_zoom = max(zoom_range)
    scale_w = int(w * max_zoom * 1.1)
    scale_h = int(h * max_zoom * 1.1)
    img = img.resize((scale_w, scale_h), Image.LANCZOS)
    img_array = np.array(img)

    z0, z1 = zoom_range
    if effect == "zoom_out":
        z0, z1 = z1, z0

    def make_frame(t):
        progress = t / max(duration, 0.001)
        progress = min(progress, 1.0)

        if effect in ("zoom_in", "zoom_out"):
            zoom = z0 + (z1 - z0) * progress
            crop_w = int(w / zoom)
            crop_h = int(h / zoom)
            cx, cy = scale_w // 2, scale_h // 2
        elif effect == "pan_left":
            zoom = (z0 + z1) / 2
            crop_w = int(w / zoom)
            crop_h = int(h / zoom)
            max_shift = (scale_w - crop_w) // 2
            cx = scale_w // 2 + int(max_shift * (1 - 2 * progress))
            cy = scale_h // 2
        elif effect == "pan_right":
            zoom = (z0 + z1) / 2
            crop_w = int(w / zoom)
            crop_h = int(h / zoom)
            max_shift = (scale_w - crop_w) // 2
            cx = scale_w // 2 - int(max_shift * (1 - 2 * progress))
            cy = scale_h // 2
        elif effect == "pan_up":
            zoom = (z0 + z1) / 2
            crop_w = int(w / zoom)
            crop_h = int(h / zoom)
            max_shift = (scale_h - crop_h) // 2
            cx = scale_w // 2
            cy = scale_h // 2 + int(max_shift * (1 - 2 * progress))
        else:
            zoom = z0
            crop_w, crop_h = int(w / zoom), int(h / zoom)
            cx, cy = scale_w // 2, scale_h // 2

        x1 = max(0, cx - crop_w // 2)
        y1 = max(0, cy - crop_h // 2)
        x2 = min(scale_w, x1 + crop_w)
        y2 = min(scale_h, y1 + crop_h)

        cropped = img_array[y1:y2, x1:x2]
        pil_crop = Image.fromarray(cropped).resize((w, h), Image.LANCZOS)
        return np.array(pil_crop)

    return VideoClip(make_frame, duration=duration).set_fps(fps)


# Ken Burns 効果のパターンをシーンごとにローテーション
KB_EFFECTS = ["zoom_in", "pan_left", "zoom_out", "pan_right", "pan_up"]


def get_kb_effect(scene_index: int) -> str:
    return KB_EFFECTS[scene_index % len(KB_EFFECTS)]


# ─────────────────────────────────────────
# テロップアニメーション
# ─────────────────────────────────────────

def create_subtitle_clip(
    text: str,
    duration: float,
    target_size: tuple[int, int] = (1080, 1920),
    font_size: int = 52,
    color: str = "white",
    bg_color: tuple[int, int, int, int] = (0, 0, 0, 160),
    position: str = "bottom",
    fade_in: float = 0.2,
    keywords: Optional[list[str]] = None,
) -> CompositeVideoClip:
    """
    字幕テロップクリップを生成する。
    keywordsが指定されたら、そのワードだけ大きく・黄色で強調する。
    """
    w, h = target_size
    font = _find_font(font_size)
    keyword_font = _find_font(int(font_size * 1.4))

    # テロップ画像を生成
    margin = 40
    max_text_w = w - margin * 2

    # テキストを折り返し
    lines = _wrap_text_pil(text, font, max_text_w)
    line_heights = [font.getbbox(line)[3] - font.getbbox(line)[1] + 8 for line in lines]
    total_h = sum(line_heights) + 20

    # 背景付きテロップ画像
    img = Image.new("RGBA", (w, total_h + 20), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 半透明背景
    draw.rectangle([(0, 0), (w, total_h + 20)], fill=bg_color)

    y_offset = 10
    for line in lines:
        # キーワード強調（単純な全角マッチ）
        if keywords:
            _draw_highlighted_line(draw, line, (margin, y_offset), font, keyword_font, keywords)
        else:
            draw.text((margin, y_offset), line, font=font, fill="white")
        y_offset += line_heights[lines.index(line)]

    img_array = np.array(img)

    clip = ImageClip(img_array, duration=duration, ismask=False, transparent=True)

    if position == "bottom":
        y_pos = h - total_h - 80  # 下から80px
    elif position == "center":
        y_pos = (h - total_h) // 2
    else:
        y_pos = 60  # top

    clip = clip.set_position(("center", y_pos))

    # フェードイン
    if fade_in > 0:
        clip = clip.crossfadein(fade_in)

    return clip


def _draw_highlighted_line(
    draw: ImageDraw.Draw,
    line: str,
    position: tuple[int, int],
    normal_font: ImageFont.FreeTypeFont,
    keyword_font: ImageFont.FreeTypeFont,
    keywords: list[str],
):
    """キーワード部分を大きく黄色で、それ以外を白で描画する"""
    x, y = position
    remaining = line

    while remaining:
        matched = False
        for kw in keywords:
            if remaining.startswith(kw):
                # キーワード強調（黄色・大きめ）
                ky_offset = -4  # 大きいフォント分の上寄せ
                draw.text((x, y + ky_offset), kw, font=keyword_font, fill=(255, 230, 50))
                bbox = keyword_font.getbbox(kw)
                x += bbox[2] - bbox[0]
                remaining = remaining[len(kw):]
                matched = True
                break
        if not matched:
            char = remaining[0]
            draw.text((x, y), char, font=normal_font, fill="white")
            bbox = normal_font.getbbox(char)
            x += bbox[2] - bbox[0]
            remaining = remaining[1:]


def _wrap_text_pil(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """PILフォントに基づいてテキストを折り返す"""
    lines = []
    current = ""
    for char in text:
        test = current + char
        bbox = font.getbbox(test)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = char
        else:
            current = test
    if current:
        lines.append(current)
    return lines


# ─────────────────────────────────────────
# 効果音生成（numpy合成・外部素材不要）
# ─────────────────────────────────────────

def generate_impact_sound(duration: float = 0.3, sample_rate: int = 44100) -> np.ndarray:
    """「ドン！」系のインパクト効果音を生成する"""
    n = int(sample_rate * duration)
    t = np.linspace(0, duration, n, endpoint=False)

    # 低音ドラム（60Hz） + サブベース（30Hz）
    signal = 0.8 * np.sin(2 * np.pi * 60 * t) * np.exp(-t * 8)
    signal += 0.4 * np.sin(2 * np.pi * 30 * t) * np.exp(-t * 5)

    # ノイズバースト（アタック感）
    noise = np.random.default_rng(0).standard_normal(n)
    signal += 0.3 * noise * np.exp(-t * 20)

    # 正規化
    signal = signal / np.max(np.abs(signal)) * 0.7
    return (signal * 32767).astype(np.int16)


def generate_whoosh_sound(duration: float = 0.4, sample_rate: int = 44100) -> np.ndarray:
    """「シュッ」系のトランジション効果音を生成する"""
    n = int(sample_rate * duration)
    t = np.linspace(0, duration, n, endpoint=False)

    # フィルタードノイズ（周波数が上がるスイープ）
    freq = 200 + 3000 * t / duration
    signal = np.sin(2 * np.pi * np.cumsum(freq / sample_rate))
    envelope = np.sin(np.pi * t / duration) ** 2  # 山型エンベロープ
    signal = signal * envelope * 0.3

    # ノイズ混合
    noise = np.random.default_rng(1).standard_normal(n)
    signal += noise * envelope * 0.1

    signal = signal / np.max(np.abs(signal)) * 0.4
    return (signal * 32767).astype(np.int16)


def generate_ping_sound(duration: float = 0.5, sample_rate: int = 44100) -> np.ndarray:
    """「ピン！」系のハイライト効果音を生成する"""
    n = int(sample_rate * duration)
    t = np.linspace(0, duration, n, endpoint=False)

    # 高音ベル（1200Hz + 倍音）
    signal = 0.6 * np.sin(2 * np.pi * 1200 * t) * np.exp(-t * 6)
    signal += 0.3 * np.sin(2 * np.pi * 2400 * t) * np.exp(-t * 8)
    signal += 0.15 * np.sin(2 * np.pi * 3600 * t) * np.exp(-t * 10)

    signal = signal / np.max(np.abs(signal)) * 0.5
    return (signal * 32767).astype(np.int16)


def sfx_to_audio_segment(samples: np.ndarray, sample_rate: int = 44100):
    """numpy 配列を pydub AudioSegment に変換する"""
    from pydub import AudioSegment

    return AudioSegment(
        samples.tobytes(),
        frame_rate=sample_rate,
        sample_width=2,
        channels=1,
    )


# ─────────────────────────────────────────
# エンゲージメントカード（CTA画面）
# ─────────────────────────────────────────

def create_engagement_card(
    quiz_question: str,
    output_path: str,
    target_size: tuple[int, int] = (1080, 1920),
    bg_image_path: Optional[str] = None,
) -> str:
    """
    動画末尾に表示するエンゲージメントカード画像を生成する。

    心理的効果:
      - 問いかけで「自分の意見を言いたい」衝動を刺激
      - 矢印 ↓ でコメント欄への視線誘導
      - 暗背景 × 白文字でフック画面との差別化（視覚的切り替え）
    """
    w, h = target_size

    # 背景: 元画像があれば暗くして使う、なければグラデーション
    if bg_image_path and Path(bg_image_path).exists():
        try:
            from PIL import ImageEnhance, ImageFilter
            bg = Image.open(bg_image_path).convert("RGB").resize((w, h), Image.LANCZOS)
            bg = ImageEnhance.Brightness(bg).enhance(0.25)
            bg = bg.filter(ImageFilter.GaussianBlur(radius=6))
        except Exception:
            bg = Image.new("RGB", (w, h), (10, 10, 25))
    else:
        bg = Image.new("RGB", (w, h), (10, 10, 25))

    draw = ImageDraw.Draw(bg)
    cx = w // 2

    f_icon    = _find_font(200)
    f_question = _find_font(72)
    f_cta     = _find_font(56)

    # ── アイコン ──
    draw.text((cx, int(h * 0.28)), "?", font=f_icon,
              fill=(255, 220, 60), anchor="mm",
              stroke_width=6, stroke_fill=(0, 0, 0))

    # ── 問いかけテキスト（中央） ──
    lines = _wrap_text_pil(quiz_question, f_question, int(w * 0.82))
    line_h = f_question.getbbox("あ")[3] + 16
    y = int(h * 0.42)
    for line in lines:
        draw.text((cx, y), line, font=f_question, fill=(255, 255, 255),
                  anchor="mt", stroke_width=5, stroke_fill=(0, 0, 0))
        y += line_h

    # ── CTA ──
    cta_y = max(y + int(h * 0.08), int(h * 0.68))
    draw.text((cx, cta_y), "コメントで教えて", font=f_cta,
              fill=(180, 220, 255), anchor="mt",
              stroke_width=4, stroke_fill=(0, 0, 0))
    draw.text((cx, cta_y + line_h + 10), "↓", font=f_icon,
              fill=(255, 220, 60), anchor="mt")

    bg.save(output_path, "JPEG", quality=92)
    logger.info(f"エンゲージメントカード生成: {output_path}")
    return output_path


# ─────────────────────────────────────────
# v2 ショート動画合成
# ─────────────────────────────────────────

def _burn_ass_subtitles(video_path: str, ass_path: str, output_path: str) -> None:
    """FFmpeg で ASS 字幕を動画に焼き込む（2パス目）"""
    import subprocess

    ass_dir = Path(ass_path).parent
    ass_name = Path(ass_path).name  # ファイル名のみ（パスエスケープ不要）

    # cwd を ass_dir にすることで Windows パス問題を回避
    ass_filter = f"ass={ass_name}"

    # プロジェクト内フォントがあれば fontsdir に指定（相対パス）
    fonts_dir = Path(__file__).parent.parent / "fonts"
    if fonts_dir.exists():
        try:
            rel = fonts_dir.resolve().relative_to(ass_dir.resolve())
            ass_filter += f":fontsdir={str(rel).replace(chr(92), '/')}"
        except ValueError:
            pass  # 相対パス化できない場合はスキップ（システムフォントで代替）

    cmd = [
        "ffmpeg", "-y",
        "-i", str(Path(video_path).resolve()),
        "-vf", ass_filter,
        "-c:a", "copy",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        str(Path(output_path).resolve()),
    ]

    logger.info(f"ASS 字幕焼き込み: {Path(output_path).name}")
    result = subprocess.run(
        cmd, cwd=str(ass_dir),
        capture_output=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        stderr_msg = (result.stderr or "")[-800:]
        raise RuntimeError(f"ASS字幕焼き込み失敗:\n{stderr_msg}")


def compose_short_video(
    scenes: list[dict],
    audio_path: str,
    output_path: str,
    target_size: tuple[int, int] = (1080, 1920),
    fps: int = 30,
    ass_path: Optional[str] = None,
) -> str:
    """
    v2 ショート動画を合成する。

    scenes: [
        {
            "image_path": str,          # 静止画（Ken Burns 適用）
            "text": str,                # ナレーションテキスト（ASS未使用時の字幕）
            "duration": float,          # 秒数
            "emotion": str,             # Surprise / Neutral / Sad / Happy / Angry
            "keywords": list[str],      # 強調キーワード（任意）
        }
    ]
    ass_path: Whisper で生成した ASS 字幕ファイルパス（指定時は FFmpeg で焼き込み）
    """
    audio = AudioFileClip(audio_path)
    total_duration = audio.duration

    # 各シーンの時間配分を調整（合計がaudio長と一致するように）
    scene_durations = [s.get("duration", total_duration / len(scenes)) for s in scenes]
    scale = total_duration / sum(scene_durations)
    scene_durations = [d * scale for d in scene_durations]

    clips = []
    t_offset = 0.0

    for i, (scene, dur) in enumerate(zip(scenes, scene_durations)):
        image_path = scene.get("image_path", "")

        if image_path and os.path.exists(image_path):
            effect = get_kb_effect(i)
            bg = ken_burns_clip(image_path, dur, target_size, effect=effect, fps=fps)
        else:
            # フォールバック: 黒背景
            bg = ImageClip(
                np.zeros((target_size[1], target_size[0], 3), dtype=np.uint8),
                duration=dur,
            ).set_fps(fps)

        # ASS字幕使用時は MoviePy の字幕をスキップ（FFmpegで後から焼き込む）
        if not ass_path:
            text = scene.get("text", "")
            keywords = scene.get("keywords", [])
            if text:
                subtitle = create_subtitle_clip(
                    text, dur, target_size, keywords=keywords, fade_in=0.2,
                )
                scene_clip = CompositeVideoClip([bg, subtitle], size=target_size)
            else:
                scene_clip = bg
        else:
            scene_clip = bg

        scene_clip = scene_clip.set_start(t_offset)
        clips.append(scene_clip)
        t_offset += dur

    # ── ループ再生用クロスフェード ──
    # 最終 0.5 秒で冒頭シーンの画像をフェードインさせ、
    # リプレイ時に視覚的に滑らかにつながるようにする
    loop_dur = 0.5
    first_img = scenes[0].get("image_path", "") if scenes else ""
    if first_img and os.path.exists(first_img) and total_duration > loop_dur * 2:
        loop_clip = ken_burns_clip(first_img, loop_dur, target_size, "zoom_in", fps=fps)
        loop_clip = loop_clip.set_start(total_duration - loop_dur).crossfadein(loop_dur)
        clips.append(loop_clip)

    # 全シーンを合成
    final = CompositeVideoClip(clips, size=target_size).set_duration(total_duration)
    final = final.set_audio(audio)

    # ASS使用時は中間ファイルに書き出してから字幕を焼き込む
    write_target = str(Path(output_path).with_suffix(".nosub.mp4")) if ass_path else output_path

    use_gpu = os.environ.get("USE_GPU_ENCODER", "").lower() in ("1", "true", "yes")
    write_kw = dict(fps=fps, audio_codec="aac", threads=4, logger=None)

    if use_gpu:
        try:
            final.write_videofile(
                write_target, codec="h264_nvenc",
                ffmpeg_params=["-preset", "p4", "-cq", "23", "-b:v", "0"],
                **write_kw,
            )
        except Exception:
            logger.warning("h264_nvenc失敗、libx264にフォールバック")
            if os.path.exists(write_target):
                os.remove(write_target)
            final.write_videofile(write_target, codec="libx264", preset="fast", **write_kw)
    else:
        final.write_videofile(write_target, codec="libx264", preset="fast", **write_kw)

    # クリーンアップ
    final.close()
    audio.close()
    for c in clips:
        c.close()

    # ASS字幕を FFmpeg で焼き込み
    if ass_path and os.path.exists(ass_path):
        _burn_ass_subtitles(write_target, ass_path, output_path)
        os.remove(write_target)

    logger.info(f"v2動画生成完了: {output_path} ({total_duration:.1f}秒)")
    return output_path
