"""
音声合成モジュール
OpenAI TTS (tts-1-hd) を優先、edge-tts をフォールバックとして使用。
和風アンビエントBGMを自動生成して音声と混合する。
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np
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
        self.provider = self.tts_config.get("provider", "edge_tts")

    def generate(self, text: str, output_path: str) -> str:
        """テキストを音声ファイル（mp3）に変換する。output_path を返す"""
        if self.provider == "openai_tts":
            if os.environ.get("OPENAI_API_KEY"):
                try:
                    return self._generate_openai_tts(text, output_path)
                except Exception as e:
                    logger.warning(f"OpenAI TTS 失敗。edge-tts にフォールバック: {e}")
            return self._generate_edge_tts(text, output_path)
        elif self.provider == "edge_tts":
            return self._generate_edge_tts(text, output_path)
        return self._generate_gtts(text, output_path)

    def _generate_gtts(self, text: str, output_path: str) -> str:
        from gtts import gTTS

        logger.info(f"gTTS で音声生成: {len(text)} 文字")
        tts = gTTS(text=text, lang=self.tts_config.get("gtts_language", "ja"), slow=False)
        tts.save(output_path)
        logger.info(f"音声保存: {output_path}")
        return output_path

    def _generate_edge_tts(self, text: str, output_path: str) -> str:
        import edge_tts

        voice = self.tts_config.get("edge_voice", self.tts_config.get("voice", "ja-JP-NanamiNeural"))
        logger.info(f"edge-tts で音声生成: voice={voice}, {len(text)} 文字")

        async def _run():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(output_path)

        asyncio.run(_run())
        logger.info(f"音声保存: {output_path}")
        return output_path

    def _generate_openai_tts(self, text: str, output_path: str) -> str:
        import openai

        voice = self.tts_config.get("voice", "nova")
        model = self.tts_config.get("openai_model", "tts-1-hd")
        logger.info(f"OpenAI TTS で音声生成: voice={voice}, model={model}, {len(text)} 文字")

        client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        response = client.audio.speech.create(
            model=model,
            voice=voice,
            input=text,
            response_format="mp3",
        )
        response.stream_to_file(output_path)
        logger.info(f"音声保存: {output_path}")
        return output_path

    # ─────────────────────────────────────────
    # BGM 生成・混合
    # ─────────────────────────────────────────

    def _generate_ambient_bgm(self, duration_sec: float) -> AudioSegment:
        """
        和風アンビエントBGMを numpy でリアルタイム生成する（外部APIキー不要）。
        Dマイナーペンタトニックの低音域を多倍音合成し、ゆっくりした振幅変調を加える。
        """
        sample_rate = 44100
        n = int(sample_rate * duration_sec)
        t = np.linspace(0, duration_sec, n, endpoint=False)

        # D マイナーペンタトニック低音域 (D2, F2, G2, A2, C3, D3)
        notes = [73.4, 87.3, 98.0, 110.0, 130.8, 146.8]
        signal = np.zeros(n, dtype=np.float64)

        rng = np.random.default_rng(42)
        for i, freq in enumerate(notes):
            # 各音ごとに異なる周波数・位相でゆっくり振幅変調
            mod_rate = 0.07 + i * 0.011
            mod_phase = i * 0.9
            envelope = 0.55 + 0.45 * np.sin(2 * np.pi * mod_rate * t + mod_phase)

            amp = 0.038 / len(notes)
            # 基音 + 2倍音 + 3倍音（自然な倍音列）
            signal += amp * envelope * (
                np.sin(2 * np.pi * freq * t)
                + 0.45 * np.sin(2 * np.pi * freq * 2 * t)
                + 0.15 * np.sin(2 * np.pi * freq * 3 * t)
            )

        # 空間的テクスチャ（非常に低レベルのホワイトノイズ）
        signal += 0.002 * rng.standard_normal(n)

        # 正規化
        max_amp = np.max(np.abs(signal))
        if max_amp > 0:
            signal = signal / max_amp * 0.45

        # フェードイン / アウト（3秒）
        fade = min(int(3 * sample_rate), n // 5)
        signal[:fade] *= np.linspace(0, 1, fade)
        signal[-fade:] *= np.linspace(1, 0, fade)

        audio_data = (signal * 32767).astype(np.int16)
        mono = AudioSegment(
            audio_data.tobytes(),
            frame_rate=sample_rate,
            sample_width=2,
            channels=1,
        )
        return mono.set_channels(2)

    def mix_with_bgm(self, voice_path: str, output_path: str) -> str:
        """ナレーション音声に和風アンビエントBGMを混合する"""
        bgm_config = self.config.get("bgm", {})
        volume_db = bgm_config.get("volume_db", -18.0)

        voice = AudioSegment.from_file(voice_path)
        duration_sec = len(voice) / 1000.0 + 2.0  # 余裕を持って生成

        bgm = self._generate_ambient_bgm(duration_sec)
        bgm = bgm[: len(voice)]  # 長さをナレーションに合わせる
        bgm = bgm + volume_db   # 音量を下げる（ナレーションを主役に）

        mixed = voice.overlay(bgm, position=0)
        mixed.export(output_path, format="mp3")
        logger.info(f"BGM 混合完了 ({volume_db:+.0f}dB): {output_path}")
        return output_path

    # ─────────────────────────────────────────
    # 速度調整・メイン実行
    # ─────────────────────────────────────────

    def adjust_speed(self, input_path: str, output_path: str, speed: float = 1.15) -> str:
        """pydub で音声の再生速度を調整する（短い動画に合わせる）"""
        if abs(speed - 1.0) < 0.01:
            import shutil
            shutil.copy2(input_path, output_path)
            return output_path

        logger.info(f"音声速度調整: {speed}x")
        audio = AudioSegment.from_file(input_path)
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
        try:
            audio = AudioSegment.from_file(audio_path)
            return len(audio) / 1000.0
        except (IndexError, Exception) as e:
            # pydub が mp3 のストリーム検出に失敗した場合、ffprobe で直接取得
            logger.warning(f"pydub読み込み失敗、ffprobeで長さを取得: {e}")
            import subprocess
            result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    audio_path,
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
            raise

    def generate_with_speed(self, text: str, output_dir: str) -> tuple[str, float]:
        """音声生成 + 速度調整 + BGM 混合をまとめて実行。(output_path, duration_sec) を返す"""
        os.makedirs(output_dir, exist_ok=True)
        raw_path = os.path.join(output_dir, "voice_raw.mp3")
        speed_path = os.path.join(output_dir, "voice_speed.mp3")
        final_path = os.path.join(output_dir, "voice.mp3")

        self.generate(text, raw_path)

        speed = self.tts_config.get("speed", 1.0)
        self.adjust_speed(raw_path, speed_path, speed)

        # BGM 混合（enabled: false で無効化可能）
        bgm_config = self.config.get("bgm", {})
        if bgm_config.get("enabled", True):
            self.mix_with_bgm(speed_path, final_path)
        else:
            import shutil
            shutil.copy2(speed_path, final_path)

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
