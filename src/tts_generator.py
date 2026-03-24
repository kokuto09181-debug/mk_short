"""
音声合成モジュール
gTTS（無料）または edge-tts（無料・高品質）で日本語音声を生成する
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path

import yaml
from pydub import AudioSegment

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"


def load_config() -> dict:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TTSGenerator:
    def __init__(self):
        self.config = load_config()
        self.tts_config = self.config["tts"]
        self.provider = self.tts_config.get("provider", "gtts")

    def generate(self, text: str, output_path: str) -> str:
        """テキストを音声ファイル（mp3）に変換する。output_path を返す"""
        if self.provider == "edge_tts":
            return self._generate_edge_tts(text, output_path)
        return self._generate_gtts(text, output_path)

    def _generate_gtts(self, text: str, output_path: str) -> str:
        from gtts import gTTS

        logger.info(f"gTTS で音声生成: {len(text)} 文字")
        tts = gTTS(text=text, lang=self.tts_config["language"], slow=False)
        tts.save(output_path)
        logger.info(f"音声保存: {output_path}")
        return output_path

    def _generate_edge_tts(self, text: str, output_path: str) -> str:
        import edge_tts

        voice = self.tts_config.get("edge_voice", "ja-JP-NanamiNeural")
        logger.info(f"edge-tts で音声生成: voice={voice}, {len(text)} 文字")

        async def _run():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(output_path)

        asyncio.run(_run())
        logger.info(f"音声保存: {output_path}")
        return output_path

    def adjust_speed(self, input_path: str, output_path: str, speed: float = 1.15) -> str:
        """pydub で音声の再生速度を調整する（短い動画に合わせる）"""
        if abs(speed - 1.0) < 0.01:
            return input_path

        logger.info(f"音声速度調整: {speed}x")
        audio = AudioSegment.from_file(input_path)
        # speedup はサンプルレート操作で実現（軽量）
        new_sample_rate = int(audio.frame_rate * speed)
        fast_audio = audio._spawn(
            audio.raw_data,
            overrides={"frame_rate": new_sample_rate},
        )
        fast_audio = fast_audio.set_frame_rate(audio.frame_rate)
        fast_audio.export(output_path, format="mp3")
        logger.info(f"速度調整済み音声: {output_path}")
        return output_path

    def get_duration(self, audio_path: str) -> float:
        """音声ファイルの長さ（秒）を返す"""
        audio = AudioSegment.from_file(audio_path)
        return len(audio) / 1000.0

    def generate_with_speed(self, text: str, output_dir: str) -> tuple[str, float]:
        """音声生成 + 速度調整をまとめて実行。(output_path, duration_sec) を返す"""
        os.makedirs(output_dir, exist_ok=True)
        raw_path = os.path.join(output_dir, "voice_raw.mp3")
        final_path = os.path.join(output_dir, "voice.mp3")

        self.generate(text, raw_path)
        speed = self.tts_config.get("speed", 1.0)
        self.adjust_speed(raw_path, final_path, speed)

        duration = self.get_duration(final_path)
        logger.info(f"最終音声: {duration:.1f}秒")
        return final_path, duration


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gen = TTSGenerator()
    path, dur = gen.generate_with_speed(
        "こんにちは、今日は面白い豆知識を紹介します。最後まで見てね！",
        "/tmp/tts_test",
    )
    print(f"生成完了: {path} ({dur:.1f}秒)")
