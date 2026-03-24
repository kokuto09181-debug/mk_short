"""
YouTube Shorts Automation - 設定ファイル
環境変数から設定を読み込む
"""
import os

# ==========================================
# API Keys (GitHub Secrets から注入)
# ==========================================
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
YOUTUBE_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET", "{}")

# ==========================================
# 動画設定
# ==========================================
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 30
VIDEO_DURATION_MAX = 58  # YouTube Shortsは60秒未満

# ==========================================
# コンテンツ設定
# ==========================================
# ニッチ選択: 収益性・自動化しやすさを考慮
# "facts"=雑学, "motivation"=名言, "tech"=テック, "money"=マネー
CONTENT_NICHE = os.environ.get("CONTENT_NICHE", "facts")

# 言語設定
CONTENT_LANGUAGE = os.environ.get("CONTENT_LANGUAGE", "ja")  # ja or en

# ==========================================
# TTS設定 (edge-tts - 無料)
# ==========================================
TTS_VOICE_JA = "ja-JP-NanamiNeural"   # 日本語女性ボイス
TTS_VOICE_EN = "en-US-JennyNeural"    # 英語女性ボイス
TTS_RATE = "+0%"    # 読み上げ速度
TTS_PITCH = "+0Hz"  # ピッチ

# ==========================================
# Claude モデル設定
# ==========================================
CLAUDE_MODEL = "claude-haiku-4-5-20251001"  # 最安値モデル

# ==========================================
# ファイルパス
# ==========================================
OUTPUT_DIR = "output"
ASSETS_DIR = "assets"
TEMPLATES_DIR = "templates"
