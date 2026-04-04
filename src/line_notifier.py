"""
LINE Messaging API 通知モジュール

動画アップロード完了・エラー・日次サマリーを LINE に送信する。

環境変数:
  LINE_CHANNEL_ACCESS_TOKEN  : チャンネルアクセストークン（必須）
  LINE_USER_ID               : 通知先ユーザーID（必須）
                               LINE Developers > Messaging API設定 > 「Your user ID」

設定がなければ何もしない（通知はオプション機能）。
"""

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

LINE_API_URL = "https://api.line.me/v2/bot/message/push"


class LineNotifier:
    """LINE Push Message を送る薄いラッパー"""

    def __init__(
        self,
        access_token: Optional[str] = None,
        user_id: Optional[str] = None,
    ):
        self.access_token = access_token or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
        self.user_id = user_id or os.environ.get("LINE_USER_ID", "")
        self.enabled = bool(self.access_token and self.user_id)

        if not self.enabled:
            logger.info("LINE通知: 環境変数未設定のためスキップ（LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID）")

    # ─────────────────────────────────────────
    # パブリックAPI
    # ─────────────────────────────────────────

    def notify_upload_success(
        self,
        name_ja: str,
        title: str,
        video_id: str,
        language: str = "ja",
        longform_video_id: str = "",
    ) -> None:
        """動画アップロード成功通知"""
        if not self.enabled:
            return

        lang_label = "🇯🇵 日本語" if language == "ja" else "🇺🇸 英語"
        text = (
            f"✅ 動画アップロード完了\n"
            f"{lang_label} | {name_ja}\n"
            f"📺 {title}"
        )
        if longform_video_id:
            text += f"\n▶ 長編: https://youtu.be/{longform_video_id}"
        self._push(text)

    def notify_error(self, name_ja: str, error: str) -> None:
        """エラー通知"""
        if not self.enabled:
            return

        short_error = error[:200] if len(error) > 200 else error
        text = (
            f"❌ 動画生成エラー\n"
            f"偉人: {name_ja}\n"
            f"エラー: {short_error}"
        )
        self._push(text)

    def notify_daily_summary(
        self,
        results: list[dict],
        dry_run: bool = False,
    ) -> None:
        """日次サマリー通知"""
        if not self.enabled:
            return

        total = len(results)
        success = sum(1 for r in results if r.get("success"))
        failed = total - success

        lines = [
            f"📊 {'[DRY RUN] ' if dry_run else ''}日次サマリー",
            f"✅ 成功: {success} 本 / 合計: {total} 本",
        ]

        if failed:
            lines.append(f"❌ 失敗: {failed} 本")

        for r in results:
            if r.get("success"):
                jp_id = r.get("jp_video_id", "")
                name = r.get("name_ja", "")
                if jp_id and jp_id != "dry_run":
                    lines.append(f"  • {name} → https://youtu.be/{jp_id}")
                else:
                    lines.append(f"  • {name} ✓")
            else:
                lines.append(f"  • {r.get('name_ja', '?')} ✗ {r.get('error', '')[:60]}")

        self._push("\n".join(lines))

    def notify_stock_warning(self, stock: int, threshold: int) -> None:
        """偉人ストック不足警告"""
        if not self.enabled:
            return

        text = (
            f"⚠️ 偉人ストック不足\n"
            f"現在: {stock} 件（閾値: {threshold} 件）\n"
            f"Notionで偉人を追加してください。"
        )
        self._push(text)

    # ─────────────────────────────────────────
    # 内部メソッド
    # ─────────────────────────────────────────

    def _push(self, text: str) -> None:
        """LINE Push Message API を呼び出す"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
        }
        payload = {
            "to": self.user_id,
            "messages": [{"type": "text", "text": text}],
        }

        try:
            resp = requests.post(LINE_API_URL, headers=headers, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.debug("LINE通知送信成功")
            else:
                logger.warning(f"LINE通知失敗: {resp.status_code} {resp.text[:200]}")
        except requests.RequestException as e:
            logger.warning(f"LINE通知エラー（ネットワーク）: {e}")
