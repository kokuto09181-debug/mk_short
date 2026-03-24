"""
TTS音声生成モジュール
edge-tts (Microsoft Edge TTS) を使用 - 完全無料
"""
import asyncio
import json
import os
from pathlib import Path

import edge_tts

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config


async def _generate_tts_async(text: str, output_path: str, voice: str, rate: str, pitch: str) -> dict:
    """edge-ttsで音声を生成し、字幕情報も返す"""
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)

    # 字幕情報収集
    subtitles = []
    audio_chunks = []

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            subtitles.append({
                "word": chunk["text"],
                "start": chunk["offset"] / 10_000_000,  # 100ns -> seconds
                "end": (chunk["offset"] + chunk["duration"]) / 10_000_000,
            })

    # 音声ファイルを書き込み
    with open(output_path, "wb") as f:
        for chunk in audio_chunks:
            f.write(chunk)

    return subtitles


def generate_voice(
    text: str,
    output_path: str,
    language: str = None,
    voice: str = None,
) -> dict:
    """
    テキストから音声ファイルを生成する

    Args:
        text: 読み上げるテキスト
        output_path: 出力MP3ファイルのパス
        language: "ja" or "en"
        voice: 使用するボイス名（省略時は言語から自動選択）

    Returns:
        {
            "audio_path": str,
            "subtitles": list[{"word": str, "start": float, "end": float}],
            "duration": float,
        }
    """
    language = language or config.CONTENT_LANGUAGE

    if voice is None:
        voice = config.TTS_VOICE_JA if language == "ja" else config.TTS_VOICE_EN

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    subtitles = asyncio.run(
        _generate_tts_async(text, output_path, voice, config.TTS_RATE, config.TTS_PITCH)
    )

    # 音声の長さを取得
    duration = 0.0
    if subtitles:
        duration = subtitles[-1]["end"]

    return {
        "audio_path": output_path,
        "subtitles": subtitles,
        "duration": duration,
    }


def build_subtitle_segments(subtitles: list, words_per_segment: int = 5) -> list:
    """
    単語レベルの字幕をセグメント（複数単語まとめ）に変換

    Args:
        subtitles: [{"word": str, "start": float, "end": float}]
        words_per_segment: 1セグメントあたりの単語数

    Returns:
        [{"text": str, "start": float, "end": float}]
    """
    if not subtitles:
        return []

    segments = []
    i = 0
    while i < len(subtitles):
        chunk = subtitles[i : i + words_per_segment]
        text = "".join(w["word"] for w in chunk)
        start = chunk[0]["start"]
        end = chunk[-1]["end"]
        segments.append({"text": text, "start": start, "end": end})
        i += words_per_segment

    return segments


if __name__ == "__main__":
    # テスト実行
    output = "output/test_voice.mp3"
    os.makedirs("output", exist_ok=True)

    result = generate_voice(
        text="タコには心臓が何個あるか知っていますか？実はタコには3つの心臓があります。フォローしてもっと面白い雑学を見てください！",
        output_path=output,
        language="ja",
    )
    print(f"音声生成完了: {result['audio_path']}")
    print(f"長さ: {result['duration']:.2f}秒")
    print(f"字幕セグメント数: {len(result['subtitles'])}")

    segments = build_subtitle_segments(result["subtitles"], words_per_segment=8)
    print("字幕サンプル:", json.dumps(segments[:3], ensure_ascii=False, indent=2))
