"""
スクリプトテキストと音声デュレーションから ASS 字幕を生成するモジュール。
Whisper（音声認識）は使わず、原稿テキストをそのまま字幕に使うことで
文字化けや幻覚を防ぐ。
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SILENCE_SEC = 0.7  # パーツ間の無音（秒）


def _format_ass_time(seconds: float) -> str:
    """ASS タイムコード (H:MM:SS.cc)"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _split_text(text: str, max_chars: int = 13) -> list[str]:
    """
    テキストを1行単位に分割する（句読点優先、最大 max_chars 文字）。
    """
    chunks = []
    current = ""
    for char in text:
        current += char
        if char in "。、！？!?…" and current:
            chunks.append(current)
            current = ""
        elif len(current) >= max_chars:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]


def _group_into_blocks(lines: list[str], lines_per_block: int = 2) -> list[str]:
    """
    1行ずつのリストを lines_per_block 行ずつのブロックにまとめる。
    ASS の改行は \\N。
    """
    blocks = []
    for i in range(0, len(lines), lines_per_block):
        block = "\\N".join(lines[i:i + lines_per_block])
        blocks.append(block)
    return blocks


def generate_ass_from_script(
    narration_parts: list[dict],
    part_durations: list[float],
    speed: float,
    output_path: str,
    target_w: int = 1080,
    target_h: int = 1920,
    font_name: str = "Yu Gothic",
    font_size: int = 60,
    lines_per_block: int = 2,
) -> str:
    """
    スクリプトのナレーションテキストと各パーツの実測デュレーションから
    ASS 字幕ファイルを生成する。

    narration_parts:  [{"text": str, "emotion": str}, ...]
    part_durations:   各パーツの秒数（速度調整前、part_XX.mp3 の長さ）
    speed:            TTS 速度倍率（1.05 など）
    lines_per_block:  字幕1イベントに表示する行数（デフォルト2行）
    """
    # YouTube Shorts UI が画面下部 ~15% に重なるため、字幕は20%マージンで配置
    margin_v = int(target_h * 0.20)

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {target_w}\n"
        f"PlayResY: {target_h}\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 0\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},{font_size},"
        "&H00FFFFFF,"  # 白文字
        "&H000000FF,"  # 未使用
        "&H00000000,"  # 黒縁取り
        "&H80000000,"  # 半透明黒背景
        f"1,0,0,0,100,100,0,0,1,4,0,2,30,30,{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    events = []
    cumulative_raw = 0.0

    for i, part in enumerate(narration_parts):
        if i >= len(part_durations):
            break

        raw_dur = part_durations[i]
        part_start = cumulative_raw / speed
        part_end = (cumulative_raw + raw_dur) / speed
        part_dur = part_end - part_start

        text = part.get("text", "").strip()
        if not text:
            cumulative_raw += raw_dur
            if i < len(narration_parts) - 1:
                cumulative_raw += _SILENCE_SEC
            continue

        lines = _split_text(text)
        blocks = _group_into_blocks(lines, lines_per_block)
        if not blocks:
            cumulative_raw += raw_dur
            if i < len(narration_parts) - 1:
                cumulative_raw += _SILENCE_SEC
            continue

        # ブロックごとの文字数（改行タグ除く）で時間を按分
        block_char_counts = [len(b.replace("\\N", "")) for b in blocks]
        total_chars = sum(block_char_counts)
        t = part_start

        for block, char_count in zip(blocks, block_char_counts):
            block_dur = part_dur * (char_count / max(total_chars, 1))
            block_end = t + block_dur
            safe_block = block.replace("{", "\\{").replace("}", "\\}")
            events.append(
                f"Dialogue: 0,{_format_ass_time(t)},{_format_ass_time(block_end)},"
                f"Default,,0,0,0,,{safe_block}"
            )
            t = block_end

        cumulative_raw += raw_dur
        if i < len(narration_parts) - 1:
            cumulative_raw += _SILENCE_SEC

    content = header + "\n".join(events) + "\n"
    Path(output_path).write_text(content, encoding="utf-8-sig")
    logger.info(f"ASS 字幕生成完了: {output_path} ({len(events)} ブロック)")
    return output_path
