"""
BGM自動ダウンロードスクリプト
Musopen (musopen.org) のパブリックドメイン音楽を
Internet Archive (archive.org) 経由でダウンロードする。

すべてパブリックドメイン（著作権なし）のため
YouTube Content ID に絶対ブロックされない。

使い方:
  python scripts/download_bgm.py
"""

import logging
import urllib.parse
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BGM_ROOT = Path(__file__).parent.parent / "data" / "bgm"
ARCHIVE_DL = "https://archive.org/download"

# ─── ムードごとの楽曲（全てMusopen パブリックドメイン）───
# 出典: Musopen.org / Internet Archive
# ライセンス: Public Domain（著作権なし）→ YouTube Content ID 対象外
MOOD_SOURCES = {
    "inspiring": {
        "identifier": "MusopenCollectionAsFlac",
        "filename": "Greig_PeerGynt/EdvardGrieg-PeerGyntSuiteNo.1Op.46-01-Morning.mp3",
        "credit": "Edvard Grieg - Morning (Peer Gynt Suite No.1) - Musopen [Public Domain]",
        "description": "科学者・発明家 / 外交官・先駆的外国人",
    },
    "empowering": {
        "identifier": "MusopenCollectionAsFlac",
        "filename": "Beethoven_EgmontOvertureOp.84/LudwigVanBeethoven-EgmontOvertureOp.84.mp3",
        "credit": "Ludwig van Beethoven - Egmont Overture Op.84 - Musopen [Public Domain]",
        "description": "女性の先駆者",
    },
    "classical": {
        "identifier": "musopen-chopin",
        "filename": "Ballade no. 1 - Op. 23.mp3",
        "credit": "Frédéric Chopin - Ballade No.1 Op.23 - Musopen [Public Domain]",
        "description": "芸術家・文化人",
    },
    "calm": {
        "identifier": "MusopenCollectionAsFlac",
        "filename": "Borodin_InTheSteppesOfCentralAsia/AlexanderBorodin-InTheSteppesOfCentralAsia.mp3",
        "credit": "Alexander Borodin - In the Steppes of Central Asia - Musopen [Public Domain]",
        "description": "医師・思想家",
    },
    "dramatic": {
        "identifier": "MusopenCollectionAsFlac",
        "filename": "Brahms_TragicOverture/JohannesBrahms-TragicOverture.mp3",
        "credit": "Johannes Brahms - Tragic Overture - Musopen [Public Domain]",
        "description": "地方の英雄・反骨者",
    },
}


def download_file(url: str, dest_path: Path) -> bool:
    resp = requests.get(url, stream=True, timeout=120)
    resp.raise_for_status()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
    return True


def download_all_bgm():
    success = 0
    for mood, cfg in MOOD_SOURCES.items():
        dest_dir = BGM_ROOT / mood
        existing = list(dest_dir.glob("*.mp3")) if dest_dir.exists() else []
        if existing:
            logger.info(f"[{mood}] スキップ（既存: {existing[0].name}）")
            success += 1
            continue

        identifier = cfg["identifier"]
        filename = cfg["filename"]
        encoded = urllib.parse.quote(filename, safe="/")
        url = f"{ARCHIVE_DL}/{identifier}/{encoded}"

        local_name = Path(filename).name
        dest_path = dest_dir / local_name

        logger.info(f"[{mood}] ダウンロード中: {cfg['credit']}")
        try:
            download_file(url, dest_path)
            size_kb = dest_path.stat().st_size // 1024
            logger.info(f"[{mood}] 保存完了: {local_name} ({size_kb}KB)")
            success += 1
        except Exception as e:
            logger.error(f"[{mood}] 失敗: {e}")
            if dest_path.exists():
                dest_path.unlink()

    logger.info(f"\n=== BGMダウンロード完了: {success}/{len(MOOD_SOURCES)} ムード ===")
    for mood, cfg in MOOD_SOURCES.items():
        dest_dir = BGM_ROOT / mood
        files = list(dest_dir.glob("*.mp3")) if dest_dir.exists() else []
        status = f"OK {files[0].name}" if files else "NG なし"
        logger.info(f"  {mood:12s}: {status}")
        if files:
            logger.info(f"               出典: {cfg['credit']}")


if __name__ == "__main__":
    download_all_bgm()
