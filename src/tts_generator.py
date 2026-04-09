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
    def __init__(self, language: str = "ja"):
        self.config = load_config()
        lang_key = "japanese" if language == "ja" else "english"
        self.tts_config = self.config["tts"].get(lang_key, {})
        self.provider = self.tts_config.get("provider", "edge_tts")

    def generate(self, text: str, output_path: str, style: str = "", style_weight: float = 0.0) -> str:
        """テキストを音声ファイル（mp3）に変換する。output_path を返す"""
        if self.provider == "irodori":
            try:
                return self._generate_irodori(text, output_path, emotion=style)
            except Exception as e:
                logger.warning(f"Irodori-TTS 失敗。edge-tts にフォールバック: {e}")
                return self._generate_edge_tts(text, output_path)
        elif self.provider == "sbv2":
            try:
                return self._generate_sbv2(text, output_path, style=style, style_weight=style_weight)
            except Exception as e:
                logger.warning(f"Style-Bert-VITS2 失敗。edge-tts にフォールバック: {e}")
                return self._generate_edge_tts(text, output_path)
        elif self.provider == "openai_tts":
            if os.environ.get("OPENAI_API_KEY"):
                try:
                    return self._generate_openai_tts(text, output_path)
                except Exception as e:
                    logger.warning(f"OpenAI TTS 失敗。edge-tts にフォールバック: {e}")
            return self._generate_edge_tts(text, output_path)
        elif self.provider == "edge_tts":
            return self._generate_edge_tts(text, output_path)
        return self._generate_gtts(text, output_path)

    # 統一ナレーションキャプション（情熱ナレーター）
    _IRODORI_CAPTION_UNIFIED = "視聴者を引き込む情感のある、少し力強い女性の声で読んでください。"

    # 感情スタイル → Irodori-TTS VoiceDesign キャプション対応表（全感情を統一）
    _IRODORI_CAPTIONS = {
        "Surprise": _IRODORI_CAPTION_UNIFIED,
        "Happy":    _IRODORI_CAPTION_UNIFIED,
        "Sad":      _IRODORI_CAPTION_UNIFIED,
        "Angry":    _IRODORI_CAPTION_UNIFIED,
        "Neutral":  _IRODORI_CAPTION_UNIFIED,
    }

    def _generate_irodori(self, text: str, output_path: str, emotion: str = "Neutral") -> str:
        """Irodori-TTS VoiceDesign でローカル音声生成（感情キャプション対応）"""
        import sys
        sys.path.insert(0, str(CONFIG_DIR.parent / "Irodori-TTS"))
        from irodori_tts.inference_runtime import InferenceRuntime, RuntimeKey, SamplingRequest, save_wav
        from huggingface_hub import hf_hub_download
        import tempfile

        cfg = self.tts_config

        if not hasattr(self, "_irodori_runtime"):
            model_path = cfg.get("irodori_model_path", "")
            if model_path:
                checkpoint = str(CONFIG_DIR.parent / model_path)
            else:
                # HuggingFaceから自動ダウンロード
                checkpoint = hf_hub_download(
                    repo_id=cfg.get("irodori_repo", "Aratako/Irodori-TTS-500M-v2-VoiceDesign"),
                    filename="model.safetensors",
                )
            key = RuntimeKey(
                checkpoint=checkpoint,
                model_device=cfg.get("irodori_device", "cuda"),
                codec_repo=cfg.get("irodori_codec_repo", "Aratako/Semantic-DACVAE-Japanese-32dim"),
                model_precision=cfg.get("irodori_precision", "bf16"),
                codec_device=cfg.get("irodori_device", "cuda"),
                codec_precision="fp32",
            )
            self._irodori_runtime = InferenceRuntime.from_key(key)
            logger.info(f"Irodori-TTS ロード完了: {cfg.get('irodori_repo', 'VoiceDesign')}")

        caption = self._IRODORI_CAPTIONS.get(emotion, self._IRODORI_CAPTIONS["Neutral"])
        seed = cfg.get("irodori_seed", 42)
        logger.info(f"Irodori-TTS 生成: emotion={emotion}, seed={seed}, {len(text)}文字")

        result = self._irodori_runtime.synthesize(
            SamplingRequest(
                text=text,
                caption=caption,
                no_ref=True,
                num_steps=cfg.get("irodori_steps", 40),
                cfg_scale_text=cfg.get("irodori_cfg_text", 3.0),
                cfg_scale_caption=cfg.get("irodori_cfg_caption", 3.0),
                seed=seed,
            )
        )

        # WAV→MP3変換
        wav_tmp = tempfile.mktemp(suffix=".wav")
        save_wav(wav_tmp, result.audio, result.sample_rate)
        from pydub import AudioSegment as AS
        seg = AS.from_wav(wav_tmp)
        seg.export(output_path, format="mp3")
        os.unlink(wav_tmp)

        logger.info(f"Irodori-TTS 保存: {output_path}")
        return output_path

    def _generate_sbv2(self, text: str, output_path: str, style: str = "", style_weight: float = 0.0) -> str:
        """Style-Bert-VITS2 でローカル音声生成（感情スタイル対応）"""
        from style_bert_vits2.tts_model import TTSModel

        cfg = self.tts_config
        sbv2_style = style or cfg.get("sbv2_default_style", "Neutral")
        sbv2_weight = style_weight or cfg.get("sbv2_default_style_weight", 4.0)

        # TTS モデルは初回のみロード（以降キャッシュ）
        if not hasattr(self, "_sbv2_model"):
            # パスはプロジェクトルートからの相対パスとして解決
            project_root = CONFIG_DIR.parent
            model_path = project_root / cfg["sbv2_model_path"]
            config_path = project_root / cfg["sbv2_config_path"]
            style_vec_path = project_root / cfg["sbv2_style_vec_path"]
            self._sbv2_model = TTSModel(
                model_path=model_path,
                config_path=config_path,
                style_vec_path=style_vec_path,
                device=cfg.get("sbv2_device", "cuda:0"),
            )
            logger.info(f"Style-Bert-VITS2 モデルロード完了: {cfg['sbv2_model_path']}")

        logger.info(f"SBV2 音声生成: style={sbv2_style}, weight={sbv2_weight}, {len(text)} 文字")

        sr, audio = self._sbv2_model.infer(
            text=text,
            style=sbv2_style,
            style_weight=sbv2_weight,
            length=cfg.get("sbv2_length", 1.05),
        )

        # WAV → MP3 変換
        import soundfile as sf
        import tempfile
        wav_tmp = tempfile.mktemp(suffix=".wav")
        sf.write(wav_tmp, audio, sr)
        from pydub import AudioSegment as AS
        seg = AS.from_wav(wav_tmp)
        seg.export(output_path, format="mp3")
        os.unlink(wav_tmp)

        logger.info(f"SBV2 音声保存: {output_path}")
        return output_path

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

        # D マイナーペンタトニック（D3〜D4: スピーカーで聴こえる可聴域）
        # D3, F3, G3, A3, C4, D4
        notes = [146.8, 174.6, 196.0, 220.0, 261.6, 293.7]
        signal = np.zeros(n, dtype=np.float64)

        rng = np.random.default_rng(42)
        for i, freq in enumerate(notes):
            # 各音ごとに異なる周波数・位相でゆっくり振幅変調
            mod_rate = 0.05 + i * 0.009
            mod_phase = i * 0.9
            envelope = 0.55 + 0.45 * np.sin(2 * np.pi * mod_rate * t + mod_phase)

            amp = 0.07 / len(notes)
            # 基音 + 2倍音 + 3倍音（自然な倍音列）
            signal += amp * envelope * (
                np.sin(2 * np.pi * freq * t)
                + 0.40 * np.sin(2 * np.pi * freq * 2 * t)
                + 0.12 * np.sin(2 * np.pi * freq * 3 * t)
            )

        # 極めて微量のホワイトノイズ（空間テクスチャ・主音を邪魔しないレベル）
        signal += 0.0003 * rng.standard_normal(n)

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
        """ナレーション音声にBGMを混合する（ファイル優先、なければアルゴリズム生成）"""
        bgm_config = self.config.get("bgm", {})
        volume_db = bgm_config.get("volume_db", -18.0)

        voice = AudioSegment.from_file(voice_path)

        # BGMファイルが指定されていればファイルから読み込む
        bgm_file = bgm_config.get("file_path", "")
        if bgm_file:
            bgm_path = CONFIG_DIR.parent / bgm_file
            if bgm_path.exists():
                bgm = AudioSegment.from_file(str(bgm_path))
                # 音声より短い場合はループ
                while len(bgm) < len(voice):
                    bgm = bgm + bgm
                bgm = bgm[: len(voice)]
                logger.info(f"BGM ファイル使用: {bgm_path.name}")
            else:
                logger.warning(f"BGMファイル未検出: {bgm_path}、アルゴリズム生成に切替")
                bgm = self._generate_ambient_bgm(len(voice) / 1000.0 + 2.0)
                bgm = bgm[: len(voice)]
        else:
            duration_sec = len(voice) / 1000.0 + 2.0
            bgm = self._generate_ambient_bgm(duration_sec)
            bgm = bgm[: len(voice)]

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
