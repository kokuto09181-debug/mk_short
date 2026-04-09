"""
note.com 自動投稿スクリプト（Playwright・保存済みセッション版）

初回のみ save_note_session.py でセッションを保存してから使う。

使い方:
  python scripts/post_to_note.py              # 未投稿の1件を公開投稿
  python scripts/post_to_note.py --name 関孝和 # 偉人名を指定
  python scripts/post_to_note.py --draft       # 下書き保存
"""

import argparse
import logging
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

load_dotenv(Path(__file__).parent.parent / ".env", encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from notion_client import NotionFigureClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

SESSION_PATH = Path(__file__).parent.parent / "data" / "note_session.json"
SECTION_SEP = "=" * 30


# ─────────────────────────────────────────
# 脚本 → テキスト変換
# ─────────────────────────────────────────

def script_to_note_content(script_text: str) -> tuple:
    """長編脚本をnoteのタイトル・本文・タグに変換する"""
    blocks = [b.strip() for b in script_text.split(SECTION_SEP) if b.strip()]

    title = ""
    description = ""
    hashtags = []
    sections = []

    for block in blocks:
        lines = block.splitlines()
        non_empty = [ln for ln in lines if ln.strip()]

        if not title and non_empty:
            for line in non_empty:
                if line.startswith("タイトル:"):
                    title = line.replace("タイトル:", "").strip()
                elif line.startswith("説明文:"):
                    description = line.replace("説明文:", "").strip()
                elif line.startswith("タグ:"):
                    raw = line.replace("タグ:", "").strip()
                    hashtags = [t.strip() for t in raw.split(",") if t.strip()]
            continue

        heading = ""
        body_lines = []
        for line in non_empty:
            if re.match(r"^【.+】", line):
                heading = line
            else:
                body_lines.append(line)

        if heading or body_lines:
            sections.append((heading, "\n".join(body_lines)))

    # YouTube URLはエディタで直接埋め込むため本文テキストには含めない
    parts = []
    if description:
        parts.append(description + "\n")
    for heading, body in sections:
        if heading:
            parts.append(f"\n{heading}\n")
        if body:
            parts.append(body + "\n")

    return title, "\n".join(parts), hashtags


def download_youtube_thumbnail(video_id: str) -> str:
    """YouTubeサムネイルをダウンロードして一時ファイルパスを返す（失敗時は空文字）"""
    for quality in ("maxresdefault", "hqdefault"):
        url = f"https://img.youtube.com/vi/{video_id}/{quality}.jpg"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = resp.read()
            if len(data) > 5000:  # プレースホルダー画像を除外
                tf = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                tf.write(data)
                tf.close()
                logger.info(f"サムネイルDL完了: {quality} ({len(data) // 1024}KB)")
                return tf.name
        except Exception as e:
            logger.warning(f"サムネイルDL失敗 ({quality}): {e}")
    return ""


# ─────────────────────────────────────────
# Playwright 投稿
# ─────────────────────────────────────────

def post_to_note(
    title: str,
    body_text: str,
    hashtags: list,
    youtube_video_id: str = "",
    thumbnail_path: str = "",
    publish: bool = True,
) -> str:
    """
    保存済みセッションを使ってnote.comに記事を投稿する。
    Returns: 投稿されたURL（失敗時は空文字）
    """
    if not SESSION_PATH.exists():
        logger.error(f"セッションファイルなし: {SESSION_PATH}")
        logger.error("先に python scripts/save_note_session.py を実行してください")
        return ""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            storage_state=str(SESSION_PATH),
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()

        try:
            # note.com トップでセッション確認
            logger.info("note.com 接続中...")
            page.goto("https://note.com/", timeout=30000)
            try:
                page.wait_for_selector('a[href="/notes/new"]', timeout=15000)
            except PWTimeout:
                pass

            post_link = page.query_selector('a[href="/notes/new"]')
            if not post_link:
                logger.error("セッション切れ。save_note_session.py を再実行してください")
                page.screenshot(path="note_error.png")
                return ""
            logger.info("セッション有効")

            # 新タブ・同タブ両対応でエディタへ遷移
            try:
                with context.expect_page(timeout=5000) as new_page_info:
                    page.click('a[href="/notes/new"]')
                editor_page = new_page_info.value
                editor_page.wait_for_load_state("domcontentloaded", timeout=30000)
            except PWTimeout:
                page.wait_for_url("**/editor.note.com/**", timeout=25000)
                editor_page = page

            logger.info(f"エディタURL: {editor_page.url}")
            if "editor.note.com" not in editor_page.url:
                logger.error(f"エディタに遷移できませんでした: {editor_page.url}")
                editor_page.screenshot(path="note_error.png")
                return ""

            page = editor_page
            page.wait_for_selector(".ProseMirror", timeout=30000)
            page.wait_for_timeout(500)

            # ── カバー画像アップロード ──────────────────────
            if thumbnail_path and os.path.exists(thumbnail_path):
                try:
                    # 円形ボタンをクリック → メニューが開く
                    page.locator('[data-dragging="false"] button').first.click()
                    page.wait_for_selector("text=画像をアップロード", timeout=3000)
                    # 「画像をアップロード」をクリックしてファイル選択ダイアログを待つ
                    with page.expect_file_chooser(timeout=5000) as fc_info:
                        page.get_by_text("画像をアップロード").click()
                    fc_info.value.set_files(thumbnail_path)
                    page.wait_for_timeout(2000)  # トリミング画面の描画を待つ
                    # 「保存」ボタンをクリック（exact=True で「下書き保存」を除外）
                    save_btn = page.get_by_role("button", name="保存", exact=True)
                    save_btn.wait_for(state="visible", timeout=15000)
                    save_btn.click()
                    page.wait_for_timeout(1000)
                    logger.info("カバー画像アップロード完了")
                except Exception as e:
                    logger.warning(f"カバー画像スキップ: {e}")
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)

            # ── タイトル入力 ────────────────────────────────
            title_el = page.locator('textarea[placeholder="記事タイトル"]')
            title_el.click()
            title_el.fill(title)
            logger.info("タイトル入力完了")
            page.wait_for_timeout(300)

            # ── 本文入力 ────────────────────────────────────
            page.click(".ProseMirror")
            page.wait_for_timeout(200)

            # YouTube埋め込み（クリップボードペーストでauto-embedを発動）
            if youtube_video_id:
                youtube_url = f"https://youtu.be/{youtube_video_id}"
                page.evaluate("(url) => navigator.clipboard.writeText(url)", youtube_url)
                page.keyboard.press("Control+v")
                page.wait_for_timeout(2000)  # embed変換を待つ
                page.keyboard.press("Enter")
                logger.info("YouTube埋め込み追加")

            for line in body_text.split("\n"):
                page.evaluate("(line) => document.execCommand('insertText', false, line)", line)
                page.keyboard.press("Enter")
            logger.info("本文入力完了")

            # ── 公開 ────────────────────────────────────────
            if publish:
                page.wait_for_selector("button:has-text('公開に進む')", timeout=10000)
                page.click("button:has-text('公開に進む')")
                logger.info("「公開に進む」クリック")

                page.wait_for_selector("button:has-text('投稿する')", timeout=10000)
                page.click("button:has-text('投稿する')")
                logger.info("「投稿する」クリック")

                # 「記事が公開されました」モーダルの×ボタンを閉じる
                try:
                    page.wait_for_selector("text=記事が公開されました", timeout=15000)
                    logger.info("公開完了モーダル表示")
                    close_btn = page.locator('button').filter(has_text="×").first
                    if not close_btn.is_visible():
                        # aria-label やsvgボタンで探す
                        close_btn = page.locator('[aria-label="閉じる"], [aria-label="close"]').first
                    close_btn.click()
                    logger.info("モーダルを閉じました")
                    page.wait_for_timeout(1000)
                except PWTimeout:
                    logger.warning("公開モーダルが見つかりませんでした（スキップ）")

                try:
                    # editor.note.com ではなく note.com 本体のURLになるまで待つ
                    page.wait_for_url("https://note.com/**", timeout=10000)
                except PWTimeout:
                    pass

                # バッジ獲得モーダルなど、note.com ホームで出るポップアップを閉じる
                try:
                    page.wait_for_selector("text=おめでとうございます", timeout=2000)
                    logger.info("バッジモーダル検出 → 閉じます")
                    page.locator("button").filter(has_text="×").first.click()
                    page.wait_for_timeout(500)
                except PWTimeout:
                    pass  # モーダルなし、正常
            else:
                page.wait_for_timeout(2000)

            note_url = page.url
            logger.info(f"投稿URL: {note_url}")
            return note_url

        except PWTimeout as e:
            logger.error(f"タイムアウト: {e}")
            page.screenshot(path="note_error.png")
        except Exception as e:
            logger.error(f"エラー: {e}")
            page.screenshot(path="note_error.png")
        finally:
            browser.close()

    return ""


# ─────────────────────────────────────────
# メイン
# ─────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="", help="偉人名を指定（1件のみ）")
    parser.add_argument("--count", type=int, default=10, help="投稿件数（デフォルト: 10）")
    parser.add_argument("--draft", action="store_true", help="下書き保存")
    args = parser.parse_args()

    notion = NotionFigureClient()
    candidates = notion.get_figures_ready_for_note(limit=200)

    if args.name:
        figures = [f for f in candidates if f.get("name_ja") == args.name]
    else:
        figures = [f for f in candidates if f.get("longform_video_id")]

    if not figures:
        print("投稿対象なし（長編動画リンクあり・note未投稿）")
        sys.exit(0)

    logger.info(f"未投稿候補: {len(figures)} 件 / 目標: {args.count} 件")
    success, skipped = 0, 0

    for figure in figures:
        if success >= args.count:
            break

        name_ja     = figure.get("name_ja", "")
        script_text = figure.get("long_script_ja", "")
        longform_id = figure.get("longform_video_id", "")
        page_id     = figure.get("page_id", "")

        logger.info(f"投稿対象: {name_ja} (YouTube: {longform_id})")

        title, body_text, hashtags = script_to_note_content(script_text)
        if not title:
            title = f"【知られざる偉人】{name_ja}の生涯"
        if not hashtags:
            hashtags = ["偉人", "日本史", "歴史"]

        thumbnail_path = download_youtube_thumbnail(longform_id) if longform_id else ""

        if not thumbnail_path:
            logger.warning(f"サムネイル取得失敗のためスキップ: {name_ja}")
            skipped += 1
            continue

        try:
            note_url = post_to_note(
                title=title,
                body_text=body_text,
                hashtags=hashtags,
                youtube_video_id=longform_id,
                thumbnail_path=thumbnail_path,
                publish=not args.draft,
            )
        finally:
            if thumbnail_path and os.path.exists(thumbnail_path):
                os.unlink(thumbnail_path)

        if note_url and "note.com" in note_url:
            print(f"[完了] {name_ja} → {note_url}")
            try:
                notion.mark_note_posted(page_id, note_url)
            except Exception as e:
                logger.error(f"Notion ステータス更新失敗: {name_ja}: {e}")
            success += 1
        else:
            print(f"[失敗] {name_ja}")
            skipped += 1

    print(f"\n=== 完了: {success} 件成功 / {skipped} 件スキップ ===")


if __name__ == "__main__":
    main()
