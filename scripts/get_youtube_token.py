"""
YouTube OAuth2 トークン取得スクリプト
お手元のPCで実行してください。

使い方:
  pip install google-auth-oauthlib google-api-python-client
  python get_youtube_token.py

実行するとブラウザが開き、Googleアカウントでログイン後に
どのYouTubeチャンネルを使うか選択できます。

日本語チャンネル用・英語チャンネル用と2回実行してください。
"""

import json
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # コメント投稿・プレイリスト操作に必要
]

def main():
    # ダウンロードしたOAuth2クライアントJSONのパスを指定
    client_secret_file = input(
        "ダウンロードしたOAuthクライアントJSONのパスを入力してください\n"
        "例: /Users/yourname/Downloads/client_secret_xxx.json\n> "
    ).strip().strip('"')

    if not os.path.exists(client_secret_file):
        print(f"ファイルが見つかりません: {client_secret_file}")
        sys.exit(1)

    channel_label = input(
        "\nどちらのチャンネル用ですか？\n"
        "  1: 日本語チャンネル (YOUTUBE_TOKEN_JSON_JP)\n"
        "  2: 英語チャンネル  (YOUTUBE_TOKEN_JSON_EN)\n> "
    ).strip()

    suffix = "JP" if channel_label == "1" else "EN"
    label = "日本語" if channel_label == "1" else "英語"

    print(f"\n{label}チャンネル用のトークンを取得します。")
    print("ブラウザが開きます。Googleアカウントにログインし、")
    print(f"「{label}チャンネル」を選択してください。\n")

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
    creds = flow.run_local_server(port=0)

    # チャンネル名を確認
    try:
        service = build("youtube", "v3", credentials=creds)
        resp = service.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items", [])
        if items:
            ch_name = items[0]["snippet"]["title"]
            print(f"\n✅ 認証成功！チャンネル名: {ch_name}")
        else:
            print("\n⚠️  チャンネルが取得できませんでした")
    except Exception as e:
        print(f"\n⚠️  チャンネル確認エラー: {e}")

    # トークンを出力
    token_data = json.loads(creds.to_json())
    token_json_str = json.dumps(token_data)
    output_file = f"youtube_token_{suffix.lower()}.json"

    with open(output_file, "w") as f:
        f.write(token_json_str)

    print(f"\n{'='*60}")
    print(f"GitHub Secretに設定する値 (YOUTUBE_TOKEN_JSON_{suffix}):")
    print(f"{'='*60}")
    print(token_json_str)
    print(f"{'='*60}")
    print(f"\nファイルにも保存しました: {output_file}")
    print("上記のJSON全体をGitHub Secretに貼り付けてください。")

if __name__ == "__main__":
    main()
