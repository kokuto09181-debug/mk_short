"""
YouTube アップロードモジュール
Google OAuth2 + YouTube Data API v3 で動画をアップロードする

認証方式:
  - GitHub Actions: サービスアカウントJSONをSecretに保存
  - ローカル: OAuth2フロー（初回のみブラウザ認証）
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # コメント投稿・プレイリスト操作に必要
]
TOKEN_CACHE_PATH = Path(__file__).parent.parent / ".youtube_token.json"


def load_config() -> dict:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


class YouTubeUploader:
    def __init__(self, channel: str = "japanese"):
        """
        channel: "japanese" or "english"
        対応する環境変数:
          YOUTUBE_CLIENT_SECRET_JSON_JP  or  YOUTUBE_CLIENT_SECRET_JSON_EN
          YOUTUBE_TOKEN_JSON_JP          or  YOUTUBE_TOKEN_JSON_EN
        """
        self.config = load_config()
        self.channel = channel
        self.channel_config = self.config["channels"][channel]
        self.upload_config = self.config["upload"]
        self._service = None

    def _get_credentials(self) -> Credentials:
        """認証情報を取得する（GitHub Actions / ローカル共用）"""
        suffix = "JP" if self.channel == "japanese" else "EN"

        # GitHub Actions: Secretから直接Tokenを読む
        token_env = os.environ.get(f"YOUTUBE_TOKEN_JSON_{suffix}")
        if token_env:
            creds_data = json.loads(token_env)
            # トークンに記録されたスコープを優先して使用（invalid_scope 防止）
            token_scopes = creds_data.get("scopes") or SCOPES
            creds = Credentials(
                token=creds_data.get("token"),
                refresh_token=creds_data.get("refresh_token"),
                token_uri=creds_data.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=creds_data.get("client_id"),
                client_secret=creds_data.get("client_secret"),
                scopes=token_scopes,
            )
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            return creds

        # ローカル: キャッシュ or OAuth2フロー
        token_path = TOKEN_CACHE_PATH.with_suffix(f".{self.channel}.json")
        client_secret_env = os.environ.get(f"YOUTUBE_CLIENT_SECRET_JSON_{suffix}")

        creds = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not client_secret_env:
                    raise ValueError(
                        f"環境変数 YOUTUBE_CLIENT_SECRET_JSON_{suffix} が設定されていません"
                    )
                client_secret_data = json.loads(client_secret_env)
                flow = InstalledAppFlow.from_client_config(client_secret_data, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(token_path, "w") as f:
                f.write(creds.to_json())

        return creds

    def _build_service(self):
        if self._service is None:
            creds = self._get_credentials()
            self._service = build("youtube", "v3", credentials=creds)
        return self._service

    def upload(
        self,
        video_path: str,
        title: str,
        description: str,
        thumbnail_path: Optional[str] = None,
        tags: Optional[list[str]] = None,
        publish_at: Optional[datetime] = None,
    ) -> str:
        """
        動画をYouTubeにアップロードして動画IDを返す。
        publish_at を指定するとスケジュール配信（指定時刻に自動公開）になる。
        失敗時は最大3回リトライ。
        """
        service = self._build_service()

        combined_tags = (self.channel_config.get("tags") or []) + (tags or [])
        category_id = self.channel_config.get("default_category", "27")
        language = self.channel_config.get("language", "ja")

        if publish_at is not None:
            # スケジュール配信: private + publishAt で指定時刻に自動公開
            publish_time = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            status = {
                "privacyStatus": "private",
                "publishAt": publish_time,
                "madeForKids": self.upload_config.get("made_for_kids", False),
                "selfDeclaredMadeForKids": False,
            }
            logger.info(f"スケジュール配信: {publish_time} (UTC)")
        else:
            status = {
                "privacyStatus": self.upload_config.get("privacy", "public"),
                "madeForKids": self.upload_config.get("made_for_kids", False),
                "selfDeclaredMadeForKids": False,
            }

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": combined_tags[:500],  # API制限
                "categoryId": category_id,
                "defaultLanguage": language,
                "defaultAudioLanguage": language,
            },
            "status": status,
        }

        media = MediaFileUpload(
            video_path,
            mimetype="video/mp4",
            resumable=True,
            chunksize=1024 * 1024 * 5,  # 5MB chunks
        )

        logger.info(f"YouTube アップロード開始 [{self.channel}]: {title}")

        for attempt in range(1, 4):
            try:
                request = service.videos().insert(
                    part="snippet,status",
                    body=body,
                    media_body=media,
                )

                response = None
                while response is None:
                    status, response = request.next_chunk()
                    if status:
                        pct = int(status.progress() * 100)
                        logger.info(f"アップロード進捗: {pct}%")

                video_id = response.get("id", "")
                logger.info(f"アップロード完了: video_id={video_id}")

                # サムネイル設定
                if thumbnail_path and video_id:
                    self._set_thumbnail(service, video_id, thumbnail_path)

                return video_id

            except HttpError as e:
                if e.resp.status in (500, 503) and attempt < 3:
                    wait = 2 ** attempt
                    logger.warning(f"アップロードエラー (attempt {attempt}), {wait}秒後にリトライ: {e}")
                    time.sleep(wait)
                else:
                    raise

        return ""

    def post_comment(self, video_id: str, comment_text: str) -> str:
        """
        動画にトップレベルコメントを投稿してコメントIDを返す。
        コメントはチャンネルオーナーとして投稿されるので
        YouTube Studioでピン留めできる。
        """
        service = self._build_service()
        try:
            response = service.commentThreads().insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id,
                        "topLevelComment": {
                            "snippet": {"textOriginal": comment_text}
                        },
                    }
                },
            ).execute()
            comment_id = response["id"]
            logger.info(f"コメント投稿完了: video_id={video_id}, comment_id={comment_id}")
            return comment_id
        except HttpError as e:
            logger.warning(f"コメント投稿失敗（スキップ）: {e}")
            return ""

    def create_or_get_playlist(self, title: str, description: str = "") -> str:
        """同名のプレイリストが既存なら playlist_id を返し、なければ新規作成して返す。"""
        service = self._build_service()
        try:
            # 既存プレイリストを検索（最大50件）
            resp = service.playlists().list(
                part="snippet",
                mine=True,
                maxResults=50,
            ).execute()
            for item in resp.get("items", []):
                if item["snippet"]["title"] == title:
                    playlist_id = item["id"]
                    logger.info(f"既存プレイリスト使用: {title} ({playlist_id})")
                    return playlist_id

            # 新規作成
            resp = service.playlists().insert(
                part="snippet,status",
                body={
                    "snippet": {"title": title, "description": description},
                    "status": {"privacyStatus": "public"},
                },
            ).execute()
            playlist_id = resp["id"]
            logger.info(f"プレイリスト作成: {title} ({playlist_id})")
            return playlist_id
        except HttpError as e:
            logger.warning(f"プレイリスト作成/取得失敗: {e}")
            return ""

    def add_to_playlist(self, playlist_id: str, video_id: str) -> bool:
        """動画をプレイリストに追加する。既に存在する場合はスキップ。"""
        if not playlist_id or not video_id:
            return False
        service = self._build_service()
        try:
            # 重複チェック
            resp = service.playlistItems().list(
                part="snippet",
                playlistId=playlist_id,
                maxResults=50,
            ).execute()
            existing_ids = {
                item["snippet"]["resourceId"]["videoId"]
                for item in resp.get("items", [])
            }
            if video_id in existing_ids:
                logger.info(f"プレイリスト追加スキップ（既存）: {video_id}")
                return True

            service.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            ).execute()
            logger.info(f"プレイリスト追加: playlist={playlist_id}, video={video_id}")
            return True
        except HttpError as e:
            logger.warning(f"プレイリスト追加失敗（スキップ）: {e}")
            return False

    def _set_thumbnail(self, service, video_id: str, thumbnail_path: str):
        """サムネイルをアップロードする"""
        try:
            media = MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
            service.thumbnails().set(
                videoId=video_id,
                media_body=media,
            ).execute()
            logger.info(f"サムネイル設定完了: video_id={video_id}")
        except HttpError as e:
            logger.warning(f"サムネイル設定失敗（スキップ）: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uploader = YouTubeUploader(channel="japanese")
    vid = uploader.upload(
        video_path="/tmp/test_video.mp4",
        title="【テスト】平賀源内の知られざる秘密",
        description="テスト動画です。\n\n#偉人 #歴史 #shorts",
        thumbnail_path="/tmp/test_thumb.jpg",
    )
    print(f"動画ID: {vid}")
