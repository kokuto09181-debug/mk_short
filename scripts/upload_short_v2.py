"""
v2ショート動画 YouTube スケジュールアップロードスクリプト

生成済み（short_v2_video_path あり）かつ未アップロードの動画を
7:00 / 12:00 / 17:00 / 21:00 JST に順番に割り当ててスケジュール配信登録する。

※ サムネイルは generate_thumbnail_v2.py で事前に生成してください。
   thumbnail.jpg が存在しない場合はサムネイルなしでアップロードします。

使い方:
  python scripts/upload_short_v2.py                # 全件（未アップロード分）
  python scripts/upload_short_v2.py --limit 4      # 最大4件
  python scripts/upload_short_v2.py --name 山川捨松 # 特定1件
  python scripts/upload_short_v2.py --dry-run       # スロット確認のみ（実際には登録しない）
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from notion_client import NotionFigureClient
from uploader import YouTubeUploader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))
OUTPUT_BASE = Path(__file__).parent.parent / "data" / "short_v2_output"
CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def next_schedule_slots(schedule_times_jst: list[str], count: int, start_from: datetime) -> list[datetime]:
    """start_from の1時間後以降から count 個のスロットを順番に返す（UTC）"""
    min_time = (start_from + timedelta(hours=1)).astimezone(JST)
    times_hm = sorted([tuple(map(int, t.split(":"))) for t in schedule_times_jst])

    slots: list[datetime] = []
    check_date = min_time.date()

    while len(slots) < count:
        for h, m in times_hm:
            slot = datetime(check_date.year, check_date.month, check_date.day, h, m, tzinfo=JST)
            if slot >= min_time:
                slots.append(slot.astimezone(timezone.utc))
                if len(slots) >= count:
                    return slots
        check_date += timedelta(days=1)
        min_time = datetime(check_date.year, check_date.month, check_date.day, tzinfo=JST)

    return slots


def load_script(output_dir: Path) -> dict:
    """output_dir/script.json を読み込む。なければ空 dict を返す"""
    path = output_dir / "script.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def build_title(script: dict, name_ja: str) -> str:
    title = script.get("title", "") or f"{name_ja}の知られざる生涯"
    return title[:100]


# ─────────────────────────────────────────────────────────────────
# 説明文（エンゲージメント最大化）
# ─────────────────────────────────────────────────────────────────

# 分野別：共感・意見誘発の問いかけテンプレート
_ENGAGEMENT_TEMPLATES: list[tuple[list[str], str]] = [
    (["女性"],     "{name}のように、時代を超えて挑戦し続けた女性の生き方。あなたならどんな選択をしますか？"),
    (["科学", "発明", "数学"], "{name}の業績、あなたは学校で習いましたか？知っていたらコメントで教えてください！"),
    (["医師", "医学", "看護"], "もし{name}がいなければ、今の医療は存在しなかったかもしれません。あなたはどう思いますか？"),
    (["芸術", "文化", "文学", "詩"], "{name}の作品や生き方、あなたはどんな印象を持ちましたか？"),
    (["外交", "外国"], "国と国の架け橋になった{name}。あなたが同じ立場だったら、同じ選択をしますか？"),
    (["教育"],    "{name}が残した教育への想い、あなたには伝わりましたか？"),
    (["地方", "反骨"], "教科書には載らなかった{name}。こういう人こそ讃えられるべきだと思いませんか？"),
]

_FALLBACK_ENGAGEMENT = "{name}のことを今日初めて知りましたか？感想をコメントで聞かせてください！"


def _pick_engagement(script: dict, name_ja: str) -> str:
    field = script.get("figure_field", "") + script.get("figure_era", "")
    for keywords, template in _ENGAGEMENT_TEMPLATES:
        if any(kw in field for kw in keywords):
            return template.replace("{name}", name_ja)
    return _FALLBACK_ENGAGEMENT.replace("{name}", name_ja)


def build_description(script: dict, name_ja: str, name_en: str, longform_video_id: str = "") -> str:
    """
    エンゲージメント最大化のYouTube説明文を構築する。

    心理的テクニック:
      ① 好奇心ギャップ : フックで「え？なぜ？」を煽る
      ② 社会的証明   : 「あなたも知らなかった」を匂わせる
      ③ 問いかけCTA  : 視聴者に明確なアクション（コメント）を促す
      ④ 視覚的構造   : 区切り線・絵文字で素早くスキャンできるレイアウト
    """
    hook       = script.get("hook", "")
    desc_base  = script.get("description", "") or f"{name_ja}の生涯と功績を紹介します。"
    series     = script.get("series_tag", "")
    era        = script.get("figure_era", "")
    field      = script.get("figure_field", "")

    engagement = _pick_engagement(script, name_ja)

    sep = "━━━━━━━━━━━━━━━━━━━━━━━━"

    parts: list[str] = []

    # ① フック（好奇心ギャップ）
    if hook:
        parts.append(hook)
        parts.append("")

    parts.append(sep)
    parts.append("")

    # ② 本文
    parts.append(desc_base)
    parts.append("")

    parts.append(sep)
    parts.append("")

    # ③ 問いかけCTA（コメント誘発）
    parts.append(f"❓ {engagement}")
    parts.append("👇 あなたの感想・意見をコメントで教えてください！")
    parts.append("")

    parts.append(sep)
    parts.append("")

    # ④ シリーズ・属性バッジ（スキャンしやすい構造）
    if series:
        parts.append(f"📚 シリーズ：{series}")
    if era or field:
        badge_parts = [p for p in [era, field] if p]
        parts.append(f"🏷  {' ／ '.join(badge_parts)}")
    parts.append("🔔 チャンネル登録で毎日1人、教科書に載らない偉人をお届けします")
    if longform_video_id:
        parts.append(f"▶ 長編で深堀り → https://youtu.be/{longform_video_id}")
    parts.append("")

    # ⑤ ハッシュタグ
    hashtags = _build_hashtags(script, name_ja)
    parts.append(hashtags)
    parts.append("")

    # ⑥ 出典
    parts.append("【参考・出典】")
    parts.append(f"・Wikipedia「{name_ja}」")
    parts.append(f"  https://ja.wikipedia.org/wiki/{name_ja}")
    if name_en:
        parts.append(f"・Wikipedia \"{name_en}\"")
        parts.append(f"  https://en.wikipedia.org/wiki/{name_en.replace(' ', '_')}")

    return "\n".join(parts)


def _build_hashtags(script: dict, name_ja: str) -> str:
    base = ["#偉人", "#歴史", "#日本史", "#雑学", "#shorts", "#知られざる偉人"]
    era    = script.get("figure_era", "")
    field  = script.get("figure_field", "")
    series = script.get("series_tag", "")
    extra = [f"#{t.replace(' ', '')}" for t in [name_ja, era, field, series] if t]
    all_tags = list(dict.fromkeys(base + extra))
    return " ".join(all_tags[:12])


def build_pinned_comment(script: dict, name_ja: str, longform_video_id: str = "") -> str:
    """
    ファーストコメント（ピン留め用）を生成する。

    伸びているチャンネルは投稿者自身が最初にコメントし、
    議論のきっかけを作ることでエンゲージメントを倍増させている。
    """
    quiz = script.get("quiz_question", "")
    if not quiz:
        quiz = f"{name_ja}のこと、あなたは知っていましたか？"

    lines = [quiz, ""]

    # 長編への誘導
    if longform_video_id:
        lines.append(f"▶ {name_ja}をもっと深く知りたい方はこちら")
        lines.append(f"https://youtu.be/{longform_video_id}")
        lines.append("")

    lines.append("知らなかった！と思った方は「いいね」で教えてください 👍")
    return "\n".join(lines)


def build_tags(script: dict, name_ja: str) -> list[str]:
    base  = ["偉人", "歴史", "日本史", "雑学", "shorts", "知られざる偉人"]
    era   = script.get("figure_era", "")
    field = script.get("figure_field", "")
    series = script.get("series_tag", "")
    extra = [t for t in [name_ja, era, field, series] if t]
    return list(dict.fromkeys(base + extra))


# ─────────────────────────────────────────────────────────────────

def run(limit: int = 50, name: str = "", dry_run: bool = False):
    notion = NotionFigureClient()
    notion.ensure_short_v2_properties()
    config = load_config()
    schedule_times = config["upload"].get(
        "short_v2_schedule_times_jst", ["07:00", "12:00", "17:00", "21:00"]
    )

    if name:
        candidates = notion.query_figures({
            "property": "Name",
            "title": {"equals": name},
        })
        if not candidates:
            print(f"Notionに「{name}」が見つかりません")
            sys.exit(1)
        figures = [notion._page_to_figure(candidates[0])]
    else:
        figures = notion.get_pending_v2_uploads(limit=limit)
        if not figures:
            print("アップロード待ちの動画がありません")
            sys.exit(0)

    total = len(figures)
    print(f"対象: {total} 件")
    for f in figures:
        print(f"  - {f['name_ja']}")
    print()

    slots = next_schedule_slots(schedule_times, total, datetime.now(timezone.utc))
    print("スケジュール割り当て:")
    for f, slot in zip(figures, slots):
        jst_str = slot.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")
        print(f"  {f['name_ja']} → {jst_str}")
    print()

    if dry_run:
        print("[DRY RUN] 実際のアップロードはスキップしました")
        return

    uploader = YouTubeUploader(channel="japanese")
    success = 0
    failed: list[str] = []

    for figure, publish_at in zip(figures, slots):
        name_ja       = figure.get("name_ja", "不明")
        name_en       = figure.get("name_en", "")
        page_id       = figure.get("page_id", "")
        video_path_str = figure.get("short_v2_video_path", "")

        video_path = (
            Path(video_path_str) if video_path_str
            else OUTPUT_BASE / name_ja / f"{name_ja}_short_v2.mp4"
        )
        jst_str = publish_at.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")
        logger.info(f"アップロード: {name_ja} → {jst_str}")

        if not video_path.exists():
            logger.error(f"動画ファイルが見つかりません: {video_path}")
            failed.append(f"{name_ja} (ファイルなし)")
            continue

        if figure.get("short_v2_youtube_id"):
            logger.warning(f"既にアップロード済みのためスキップ: {name_ja}")
            continue

        output_dir       = video_path.parent
        script           = load_script(output_dir)
        longform_vid     = figure.get("longform_video_id", "")
        title            = build_title(script, name_ja)
        description      = build_description(script, name_ja, name_en, longform_vid)
        tags             = build_tags(script, name_ja)
        pinned_comment   = build_pinned_comment(script, name_ja, longform_vid)

        try:
            # YouTube Shorts はカスタムサムネイルのアップロード不可
            # → サムネイル画像は動画冒頭フレームとして焼き込み済み
            video_id = uploader.upload(
                video_path=str(video_path),
                title=title,
                description=description,
                thumbnail_path=None,
                tags=tags,
                publish_at=publish_at,
            )

            # ⑤ ピン留めコメント
            # スケジュール配信（private）の動画にはコメント投稿不可のため
            # Notion に保存し、公開後に --post-comments で一括投稿する
            if page_id:
                notion.mark_v2_uploaded(page_id, video_id, publish_at, pinned_comment)
            success += 1
            print(f"[{success}/{total}] 完了: {name_ja} → {video_id} ({jst_str})")

        except Exception as e:
            logger.error(f"アップロード失敗: {name_ja}: {e}", exc_info=True)
            print(f"エラー: {name_ja} - {e}")
            failed.append(name_ja)

    print(f"\n{'='*40}")
    print(f"完了: {success}/{total} 件アップロード")
    if failed:
        print(f"失敗 ({len(failed)} 件):")
        for n in failed:
            print(f"  - {n}")


def post_pending_comments():
    """公開時刻を過ぎた動画のピン留めコメントをまとめて投稿する"""
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")

    notion   = NotionFigureClient()
    uploader = YouTubeUploader(channel="japanese")
    figures  = notion.get_pending_comment_figures()

    if not figures:
        print("コメント投稿待ちの動画はありません")
        return

    print(f"コメント投稿待ち: {len(figures)} 件")
    ok = 0
    for fig in figures:
        name_ja    = fig.get("name_ja", "")
        page_id    = fig.get("page_id", "")
        video_id   = fig.get("short_v2_youtube_id", "")
        comment    = fig.get("short_v2_pinned_comment", "")
        sched      = fig.get("short_v2_scheduled_at", "")
        if not video_id or not comment:
            continue
        print(f"  投稿: {name_ja} ({video_id}) 予定:{sched}")
        cid = uploader.post_comment(video_id, comment)
        if cid:
            notion.mark_comment_posted(page_id)
            ok += 1
            print(f"    ✅ 完了: comment_id={cid}")
        else:
            print(f"    ❌ 失敗（動画がまだ非公開の可能性）")

    print(f"\n完了: {ok}/{len(figures)} 件")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="v2ショート動画 YouTube スケジュールアップロード",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
例:
  python scripts/upload_short_v2.py                 # 全未アップロード分
  python scripts/upload_short_v2.py --limit 8       # 最大8件（2日分）
  python scripts/upload_short_v2.py --name 山川捨松  # 特定1件
  python scripts/upload_short_v2.py --dry-run        # スロット確認のみ
        """,
    )
    parser.add_argument("--limit",         type=int,  default=50,  help="最大処理件数（デフォルト: 50）")
    parser.add_argument("--name",          type=str,  default="",  help="特定の偉人名を指定して1件のみ処理")
    parser.add_argument("--dry-run",       action="store_true",    help="スロット割り当て確認のみ")
    parser.add_argument("--post-comments", action="store_true",    help="公開済み動画のピン留めコメントを投稿")
    args = parser.parse_args()

    if args.post_comments:
        post_pending_comments()
    else:
        run(limit=args.limit, name=args.name, dry_run=args.dry_run)
