"""
note.com セッション保存スクリプト（初回1回のみ実行）

ブラウザを開いて手動でログインし、セッションを保存します。
次回以降の自動投稿はこのセッションを使うのでログイン不要になります。

使い方:
  python scripts/save_note_session.py
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

SESSION_PATH = Path(__file__).parent.parent / "data" / "note_session.json"
SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)

print("ブラウザを開きます。note.comにログインしてください。")
print("ログイン完了後、ブラウザを閉じずに Enter を押してください。")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)  # ブラウザを表示
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://note.com/login")

    input(">>> note.com にログインしたら Enter を押してください...")

    # セッション保存
    context.storage_state(path=str(SESSION_PATH))
    print(f"セッション保存完了: {SESSION_PATH}")
    browser.close()

print("完了！次回から python scripts/post_to_note.py で自動投稿できます。")
