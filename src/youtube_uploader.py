"""
YouTube アップロードモジュール
YouTube Data API v3 を使って動画をアップロードする

認証方式:
- OAuth2 (推奨): ユーザーが初回だけブラウザで認証
- Service Account: GitHub Actionsでの自動化に適している

セットアップ手順:
1. Google Cloud Console でプロジェクトを作成
2. YouTube Data API v3 を有効化
3. OAuth 2.0 クライアントIDを作成（デスクトップアプリ）
4. client_secret.json をダウンロード
5. 初回: python src/youtube_uploader.py --auth
   -> ブラウザで認証 -> token.json が生成される
6. token.json を GitHub Secret (YOUTUBE_TOKEN_JSON) に設定
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    YOUTUBE_AVAILABLE = True
except ImportError:
    YOUTUBE_AVAILABLE = False


SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = "token.json"
CLIENT_SECRET_FILE = "client_secret.json"


def _get_credentials():
    """OAuth2認証情報を取得"""
    creds = None

    # GitHub Actions: 環境変数からトークンを読み込む
    token_json_env = os.environ.get("YOUTUBE_TOKEN_JSON")
    if token_json_env:
        token_data = json.loads(token_json_env)
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)

    # ローカル: token.json ファイルから読み込む
    elif os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # トークンが期限切れの場合はリフレッシュ
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # 更新したトークンを保存
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())

    return creds


def authenticate():
    """
    初回認証フロー（ローカルで一度だけ実行）
    ブラウザが開いてGoogleアカウントでログインする
    """
    if not YOUTUBE_AVAILABLE:
        raise ImportError("google-api-python-client が必要です: pip install google-api-python-client google-auth-oauthlib")

    # クライアントシークレットの読み込み
    client_secret_data = os.environ.get("YOUTUBE_CLIENT_SECRET", "{}")
    client_secret = json.loads(client_secret_data)

    if not client_secret.get("installed") and os.path.exists(CLIENT_SECRET_FILE):
        with open(CLIENT_SECRET_FILE) as f:
            client_secret = json.load(f)

    if not client_secret.get("installed"):
        raise FileNotFoundError(
            "client_secret.json が見つかりません。\n"
            "Google Cloud Console からOAuth2クライアントIDをダウンロードしてください。"
        )

    flow = InstalledAppFlow.from_client_config(client_secret, SCOPES)
    creds = flow.run_local_server(port=0)

    # トークンを保存
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print(f"認証完了！token.json に保存しました。")
    print("このファイルの内容を GitHub Secret の YOUTUBE_TOKEN_JSON に設定してください。")
    print(f"\ntoken.json の内容:\n{creds.to_json()}")
    return creds


def upload_short(
    video_path: str,
    title: str,
    description: str,
    tags: list,
    category_id: str = "22",  # 22 = People & Blogs
    privacy_status: str = "public",
) -> dict:
    """
    YouTube Shortsとして動画をアップロードする

    Args:
        video_path: アップロードする動画ファイルパス
        title: 動画タイトル
        description: 動画説明文
        tags: タグリスト
        category_id: カテゴリID
        privacy_status: "public", "private", "unlisted"

    Returns:
        {"video_id": str, "url": str, "status": str}
    """
    if not YOUTUBE_AVAILABLE:
        print("[SKIP] YouTube APIライブラリが未インストール。アップロードをスキップします。")
        return {"video_id": "MOCK_ID", "url": "https://youtube.com/shorts/MOCK_ID", "status": "skipped"}

    creds = _get_credentials()
    if not creds:
        print("[SKIP] YouTube認証情報がありません。アップロードをスキップします。")
        return {"video_id": None, "url": None, "status": "no_auth"}

    try:
        youtube = build("youtube", "v3", credentials=creds)

        # ShortsはタイトルまたはDescriptionに#Shortsを含める
        if "#Shorts" not in description and "#shorts" not in description:
            description = "#Shorts\n\n" + description

        body = {
            "snippet": {
                "title": title[:100],  # YouTubeは100文字制限
                "description": description[:5000],
                "tags": tags + ["Shorts", "shorts"],
                "categoryId": category_id,
                "defaultLanguage": config.CONTENT_LANGUAGE,
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            video_path,
            mimetype="video/mp4",
            resumable=True,
            chunksize=1024 * 1024,  # 1MB chunks
        )

        request = youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                print(f"アップロード中: {progress}%")

        video_id = response["id"]
        url = f"https://www.youtube.com/shorts/{video_id}"
        print(f"アップロード完了: {url}")

        return {"video_id": video_id, "url": url, "status": "success"}

    except HttpError as e:
        print(f"YouTube APIエラー: {e}")
        return {"video_id": None, "url": None, "status": f"error: {e}"}


def build_description(script: dict, niche: str = None) -> str:
    """スクリプトから動画説明文を生成"""
    tags_str = " ".join(script.get("tags", []))
    body = script.get("body", "")
    cta = script.get("cta", "")

    desc_parts = []
    if body:
        desc_parts.append(body)
    if cta:
        desc_parts.append(cta)
    desc_parts.append("")
    desc_parts.append(tags_str)
    desc_parts.append("")
    desc_parts.append("─────────────────")
    desc_parts.append("毎日ためになる動画を投稿中！チャンネル登録お願いします。")

    return "\n".join(desc_parts)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="YouTube認証・アップロードツール")
    parser.add_argument("--auth", action="store_true", help="初回OAuth2認証を実行")
    parser.add_argument("--upload", type=str, help="アップロードする動画ファイルパス")
    parser.add_argument("--title", type=str, default="テスト動画", help="動画タイトル")
    args = parser.parse_args()

    if args.auth:
        authenticate()
    elif args.upload:
        result = upload_short(
            video_path=args.upload,
            title=args.title,
            description="#Shorts テスト投稿",
            tags=["テスト", "shorts"],
        )
        print(result)
    else:
        parser.print_help()
