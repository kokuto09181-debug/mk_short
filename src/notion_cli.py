"""
Notion CLI — スケジュールエージェント用ヘルパー

使い方:
  # 脚本未生成の偉人を JSON で出力
  python src/notion_cli.py fetch-pending [--limit N]

  # 脚本を Notion に書き込む
  python src/notion_cli.py write-script --page-id ID --script-ja '...' --script-en '...'

  # script_ja / script_en プロパティをDBに追加（初回のみ）
  python src/notion_cli.py migrate
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from notion_client import NotionFigureClient

logging.basicConfig(level=logging.WARNING)


def cmd_fetch_pending(args):
    client = NotionFigureClient()
    figures = client.get_pending_without_scripts(limit=args.limit)
    print(json.dumps(figures, ensure_ascii=False, indent=2))


def cmd_write_script(args):
    client = NotionFigureClient()
    # JSONとして正しいか検証
    try:
        json.loads(args.script_ja)
        json.loads(args.script_en)
    except json.JSONDecodeError as e:
        print(f"ERROR: 不正なJSON: {e}", file=sys.stderr)
        sys.exit(1)

    client.write_scripts(args.page_id, args.script_ja, args.script_en)
    print(f"OK: {args.page_id}")


def cmd_migrate(args):
    client = NotionFigureClient()
    client.ensure_script_properties()
    print("OK: script_ja / script_en プロパティを追加しました")


def main():
    parser = argparse.ArgumentParser(description="Notion CLI for script management")
    subparsers = parser.add_subparsers(dest="command")

    # fetch-pending
    fp = subparsers.add_parser("fetch-pending", help="脚本未生成の偉人を取得")
    fp.add_argument("--limit", type=int, default=10)

    # write-script
    ws = subparsers.add_parser("write-script", help="脚本をNotionに書き込む")
    ws.add_argument("--page-id", required=True)
    ws.add_argument("--script-ja", required=True)
    ws.add_argument("--script-en", required=True)

    # migrate
    subparsers.add_parser("migrate", help="DBにscriptプロパティを追加")

    args = parser.parse_args()

    if args.command == "fetch-pending":
        cmd_fetch_pending(args)
    elif args.command == "write-script":
        cmd_write_script(args)
    elif args.command == "migrate":
        cmd_migrate(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
