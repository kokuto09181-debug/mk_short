"""
v2ショート動画サムネイル生成モジュール

script.json のテキスト + images/ の背景画像から
視覚的・心理的効果を最大化したサムネイル（1080x1920）を生成する。

【心理的テクニック】
  ① 好奇心ギャップ: thumbnail_text に「？」で「なぜ？」を煽る
  ② コントラスト  : 暗め背景 × 白・黄文字で高視認性
  ③ 情報の階層化  : 大→中→小フォントで一瞬でスキャン可能
  ④ 未完感       : 答えを見せず「続きは動画で」という余白
"""

import logging
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageEnhance

logger = logging.getLogger(__name__)

# ─── フォント ───────────────────────────────────────────────────
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/YuGothB.ttc",
    "C:/Windows/Fonts/BIZ-UDGothicB.ttc",
    "C:/Windows/Fonts/meiryob.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ─── テキスト描画 ──────────────────────────────────────────────

def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for ch in text:
        test = current + ch
        if font.getbbox(test)[2] > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = test
    if current:
        lines.append(current)
    return lines


def _draw_text_stroked(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple,
    stroke_fill: tuple = (0, 0, 0),
    stroke_width: int = 6,
    anchor: str = "mm",
):
    draw.text(xy, text, font=font, fill=stroke_fill,
              stroke_width=stroke_width, stroke_fill=stroke_fill, anchor=anchor)
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def _draw_multiline_centered(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    fill: tuple,
    center_x: int,
    top_y: int,
    line_spacing: int = 12,
    stroke_width: int = 6,
    stroke_fill: tuple = (0, 0, 0),
) -> int:
    """複数行を中央揃えで描画し、最下端のY座標を返す"""
    line_h = font.getbbox("あ")[3] + line_spacing
    y = top_y
    for line in lines:
        _draw_text_stroked(
            draw, (center_x, y + line_h // 2),
            line, font, fill,
            stroke_fill=stroke_fill, stroke_width=stroke_width,
            anchor="mm",
        )
        y += line_h
    return y


def _draw_badge(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    cx: int, cy: int,
    bg_color: tuple = (180, 50, 30, 220),
    text_color: tuple = (255, 255, 255),
    pad_x: int = 28, pad_y: int = 14,
):
    tw, th = font.getbbox(text)[2], font.getbbox(text)[3]
    x0, y0 = cx - tw // 2 - pad_x, cy - th // 2 - pad_y
    x1, y1 = cx + tw // 2 + pad_x, cy + th // 2 + pad_y
    draw.rounded_rectangle([x0, y0, x1, y1], radius=16, fill=bg_color)
    draw.text((cx, cy), text, font=font, fill=text_color, anchor="mm")


# ─── グラデーションオーバーレイ ───────────────────────────────

def _apply_gradient_overlay(img: Image.Image, w: int, h: int) -> Image.Image:
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    grad_top = int(h * 0.40)
    for y in range(grad_top):
        alpha = int(190 * (1.0 - y / grad_top) ** 1.5)
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))
    grad_bot_start = int(h * 0.55)
    for y in range(grad_bot_start, h):
        t = (y - grad_bot_start) / (h - grad_bot_start)
        alpha = int(230 * t ** 1.2)
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


# ─── 背景画像ロード ───────────────────────────────────────────

def _load_bg(images_dir: Path, w: int, h: int) -> Image.Image:
    candidates = (
        list(images_dir.glob("wiki_00.*"))
        + list(images_dir.glob("wiki_0*.*"))
        + list(images_dir.glob("bg_00.*"))
        + list(images_dir.glob("bg_0*.*"))
    )
    valid_exts = {".jpg", ".jpeg", ".png", ".webp"}
    candidates = [p for p in candidates if p.suffix.lower() in valid_exts]

    if candidates:
        try:
            img = Image.open(candidates[0]).convert("RGB")
            orig_w, orig_h = img.size
            target_ratio = w / h
            orig_ratio = orig_w / orig_h
            if orig_ratio > target_ratio:
                new_w = int(orig_h * target_ratio)
                left = (orig_w - new_w) // 2
                img = img.crop((left, 0, left + new_w, orig_h))
            else:
                new_h = int(orig_w / target_ratio)
                img = img.crop((0, 0, orig_w, min(new_h, orig_h)))
            img = img.resize((w, h), Image.LANCZOS)
            img = ImageEnhance.Brightness(img).enhance(0.50)
            img = img.filter(ImageFilter.GaussianBlur(radius=3))
            return img
        except Exception as e:
            logger.warning(f"背景画像読み込み失敗: {e}")

    # フォールバック: グラデーション背景
    base = Image.new("RGB", (w, h), (10, 8, 20))
    draw = ImageDraw.Draw(base)
    for y in range(h):
        t = y / h
        draw.line([(0, y), (w, y)], fill=(int(10 + 20 * t), int(8 + 12 * t), int(20 + 40 * t)))
    return base


# ─── メイン生成関数 ────────────────────────────────────────────

def create_thumbnail(
    name_ja: str,
    script: dict,
    output_dir: Path,
    width: int = 1080,
    height: int = 1920,
) -> str:
    """
    サムネイルを生成して output_dir/thumbnail.jpg に保存し、パスを返す。

    レイアウト（上から）:
      上部  : シリーズバッジ（赤）
      中央上: フックテキスト（黄・最大）← 好奇心ギャップ
      中央下: 偉人名（白・大）
      下部  : 時代・分野バッジ（青）
    """
    w, h = width, height
    cx = w // 2

    bg = _load_bg(output_dir / "images", w, h)
    bg = _apply_gradient_overlay(bg, w, h)
    canvas = bg.copy()
    draw = ImageDraw.Draw(canvas)

    f_series = _load_font(44)
    f_hook   = _load_font(108)
    f_name   = _load_font(130)
    f_badge  = _load_font(42)

    # シリーズバッジ（上部）
    series = script.get("series_tag", "教科書に載らない偉人")
    if series:
        _draw_badge(draw, series, f_series, cx, int(h * 0.09),
                    bg_color=(180, 45, 25, 230), text_color=(255, 255, 255),
                    pad_x=32, pad_y=16)

    # フックテキスト（中央上）
    hook_raw = (
        script.get("thumbnail_text")
        or script.get("hook", "")[:20]
        or name_ja
    )
    if hook_raw and not hook_raw.endswith(("？", "！", "…", "。")):
        hook_raw = hook_raw.rstrip("?!.") + "？"
    hook_lines = _wrap_text(hook_raw, f_hook, int(w * 0.88))
    hook_y_end = _draw_multiline_centered(
        draw, hook_lines, f_hook,
        fill=(255, 240, 60),
        center_x=cx, top_y=int(h * 0.22),
        line_spacing=16, stroke_width=8,
    )

    # 偉人名（中央下）
    name_y_start = max(hook_y_end + int(h * 0.04), int(h * 0.54))
    name_lines   = _wrap_text(name_ja, f_name, int(w * 0.88))
    name_y_end   = _draw_multiline_centered(
        draw, name_lines, f_name,
        fill=(255, 255, 255),
        center_x=cx, top_y=name_y_start,
        line_spacing=12, stroke_width=9,
    )

    # 時代・分野バッジ（下部）
    era   = script.get("figure_era", "")
    field = script.get("figure_field", "")
    badge_text = " ／ ".join(p for p in [era, field] if p)
    if badge_text:
        attr_y = max(name_y_end + int(h * 0.04), int(h * 0.82))
        _draw_badge(draw, badge_text, f_badge, cx, attr_y,
                    bg_color=(30, 80, 160, 210), text_color=(200, 230, 255),
                    pad_x=28, pad_y=14)

    output_path = str(output_dir / "thumbnail.jpg")
    canvas.save(output_path, "JPEG", quality=95)
    logger.info(f"サムネイル保存: {output_path}")
    return output_path
