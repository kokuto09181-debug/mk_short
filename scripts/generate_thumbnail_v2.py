"""
v2ショート動画 サムネイル生成スクリプト（CLIラッパー）

generate_short_v2.py が動画生成時に自動実行するため、
単体で再生成したい場合のみこのスクリプトを使用する。

使い方:
  python scripts/generate_thumbnail_v2.py --name 山川捨松
  python scripts/generate_thumbnail_v2.py --all           # 未生成を全件
  python scripts/generate_thumbnail_v2.py --all --force   # 全件上書き再生成
  python scripts/generate_thumbnail_v2.py --name 山川捨松 --preview
"""

import argparse
import json
import logging
import sys
from pathlib import Path

_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root / "src"))

from thumbnail_generator import create_thumbnail
from PIL import Image

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

OUTPUT_BASE = _root / "data" / "short_v2_output"


def process_one(name_ja: str, force: bool = False, preview: bool = False) -> bool:
    output_dir  = OUTPUT_BASE / name_ja
    script_path = output_dir / "script.json"
    thumb_path  = output_dir / "thumbnail.jpg"

    if not script_path.exists():
        logger.error(f"script.json が見つかりません: {script_path}")
        return False

    if thumb_path.exists() and not force:
        logger.info(f"既存サムネイルをスキップ（--force で上書き）: {name_ja}")
        return True

    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)

    out = create_thumbnail(name_ja, script, output_dir)
    print(f"生成: {out}")

    if preview:
        try:
            Image.open(out).show()
        except Exception:
            pass

    return True


def main():
    parser = argparse.ArgumentParser(
        description="v2ショートサムネイル生成（単体実行用）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
例:
  python scripts/generate_thumbnail_v2.py --name 山川捨松
  python scripts/generate_thumbnail_v2.py --all
  python scripts/generate_thumbnail_v2.py --all --force
  python scripts/generate_thumbnail_v2.py --name 山川捨松 --preview
        """,
    )
    parser.add_argument("--name",    type=str, default="", help="偉人名を指定して1件のみ処理")
    parser.add_argument("--all",     action="store_true",  help="short_v2_output 以下を全件処理")
    parser.add_argument("--force",   action="store_true",  help="既存 thumbnail.jpg を上書き再生成")
    parser.add_argument("--preview", action="store_true",  help="生成後にビューアで表示")
    args = parser.parse_args()

    if args.name:
        process_one(args.name, force=args.force, preview=args.preview)

    elif args.all:
        dirs = sorted(OUTPUT_BASE.iterdir()) if OUTPUT_BASE.exists() else []
        targets = [d for d in dirs if d.is_dir() and (d / "script.json").exists()]
        print(f"対象: {len(targets)} 件")
        success = 0
        for d in targets:
            try:
                if process_one(d.name, force=args.force):
                    success += 1
            except Exception as e:
                logger.error(f"失敗: {d.name}: {e}", exc_info=True)
        print(f"\n完了: {success}/{len(targets)} 件")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
