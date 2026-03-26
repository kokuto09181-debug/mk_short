"""
Notion 連携モジュール
偉人リストの管理・制作済みフラグ・重複防止を担当する

Notion DB スキーマ:
  - Name (title)          : 偉人名（日本語）
  - name_en (rich_text)   : 偉人名（英語）
  - birth_year (number)   : 生年
  - death_year (number)   : 没年
  - era (select)          : 時代
  - field (select)        : 分野
  - notes (rich_text)     : 特筆事項
  - status (select)       : pending / producing / done / error
  - jp_video_id (rich_text): YouTube動画ID（日本語）
  - en_video_id (rich_text): YouTube動画ID（英語）
  - produced_at (date)    : 制作日
  - title_ja (rich_text)  : 動画タイトル（日本語）
  - title_en (rich_text)  : 動画タイトル（英語）
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionFigureClient:
    def __init__(self, token: Optional[str] = None, database_id: Optional[str] = None):
        self.token = token or os.environ["NOTION_TOKEN"]
        self.database_id = database_id or os.environ["NOTION_DATABASE_ID"]
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        })
        self._error_log_ensured = False

    # ─────────────────────────────────────────
    # 内部ヘルパー
    # ─────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def _post(self, endpoint: str, payload: dict) -> dict:
        resp = self.session.post(f"{NOTION_API_BASE}/{endpoint}", json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def _patch(self, endpoint: str, payload: dict) -> dict:
        resp = self.session.patch(f"{NOTION_API_BASE}/{endpoint}", json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def _get(self, endpoint: str) -> dict:
        resp = self.session.get(f"{NOTION_API_BASE}/{endpoint}", timeout=15)
        resp.raise_for_status()
        return resp.json()

    # ─────────────────────────────────────────
    # DB 操作
    # ─────────────────────────────────────────

    def query_figures(self, filter_payload: Optional[dict] = None) -> list[dict]:
        """DB を検索してページリストを返す（全ページ取得）"""
        results = []
        start_cursor = None

        while True:
            body: dict = {"page_size": 100}
            if filter_payload:
                body["filter"] = filter_payload
            if start_cursor:
                body["start_cursor"] = start_cursor

            data = self._post(f"databases/{self.database_id}/query", body)
            results.extend(data.get("results", []))

            if not data.get("has_more"):
                break
            start_cursor = data.get("next_cursor")

        return results

    def get_pending_figures(self, limit: int = 10) -> list[dict]:
        """status=pending または error の偉人を取得する（error は再試行）"""
        data = self.query_figures({
            "or": [
                {"property": "status", "select": {"equals": "pending"}},
                {"property": "status", "select": {"equals": "error"}},
            ]
        })
        figures = [self._page_to_figure(p) for p in data]
        pending = [f for f in figures if f["status"] == "pending"]
        errors  = [f for f in figures if f["status"] == "error"]
        logger.info(f"取得: pending={len(pending)}件, error={len(errors)}件（再試行）")
        # pending を優先し、不足分を error で補う
        return (pending + errors)[:limit]

    def reset_stale_producing(self):
        """中断等でproducingのまま残った偉人をpendingに戻す"""
        data = self.query_figures({
            "property": "status",
            "select": {"equals": "producing"},
        })
        for page in data:
            self._patch(f"pages/{page['id']}", {
                "properties": {"status": {"select": {"name": "pending"}}}
            })
        if data:
            logger.info(f"{len(data)}件のproducing状態をpendingにリセット")

    def get_all_names_ja(self) -> list[str]:
        """重複チェック用：DB 内の全日本語名リストを返す"""
        pages = self.query_figures()
        return [
            self._get_prop_text(p["properties"], "Name")
            for p in pages
            if self._get_prop_text(p["properties"], "Name")
        ]

    def add_figures(self, figures: list[dict]) -> list[str]:
        """偉人リストをまとめてDBに追加。追加したページIDのリストを返す"""
        ids = []
        for fig in figures:
            page_id = self.add_figure(fig)
            if page_id:
                ids.append(page_id)
        logger.info(f"{len(ids)} 件の偉人を追加")
        return ids

    def add_figure(self, figure: dict) -> Optional[str]:
        """1件の偉人をDBに追加。ページIDを返す"""
        props = {
            "Name": {"title": [{"text": {"content": figure.get("name_ja", "")}}]},
            "name_en": {"rich_text": [{"text": {"content": figure.get("name_en", "")}}]},
            "era": {"select": {"name": figure.get("era", "不明")}},
            "field": {"select": {"name": figure.get("field", "その他")}},
            "notes": {"rich_text": [{"text": {"content": figure.get("notes", "")[:2000]}}]},
            "status": {"select": {"name": "pending"}},
        }
        if figure.get("birth_year"):
            props["birth_year"] = {"number": int(figure["birth_year"])}
        if figure.get("death_year"):
            props["death_year"] = {"number": int(figure["death_year"])}

        result = self._post("pages", {
            "parent": {"database_id": self.database_id},
            "properties": props,
        })
        page_id = result.get("id")
        logger.info(f"追加: {figure.get('name_ja')} (id={page_id})")
        return page_id

    @staticmethod
    def _json_to_rich_text(obj: dict) -> list[dict]:
        """dictをJSON文字列化してNotionのrich_textブロック（2000字制限対応）に変換"""
        import json as _json
        text = _json.dumps(obj, ensure_ascii=False)
        return [{"text": {"content": text[i:i+2000]}} for i in range(0, len(text), 2000)]

    def save_scripts(self, page_id: str, script_ja: dict, script_en: dict):
        """台本（日英）をNotionに保存する"""
        self._ensure_script_properties()
        self._patch(f"pages/{page_id}", {
            "properties": {
                "script_ja": {"rich_text": self._json_to_rich_text(script_ja)},
                "script_en": {"rich_text": self._json_to_rich_text(script_en)},
            }
        })
        logger.info(f"台本保存: page_id={page_id}")

    def get_scripts(self, page_id: str) -> tuple[dict | None, dict | None]:
        """NotionページからJSONパース済み台本（日英）を返す。未保存なら(None, None)"""
        import json as _json
        page = self._get(f"pages/{page_id}")
        props = page.get("properties", {})
        ja_text = self._get_prop_text(props, "script_ja")
        en_text = self._get_prop_text(props, "script_en")
        try:
            script_ja = _json.loads(ja_text) if ja_text else None
        except Exception:
            script_ja = None
        try:
            script_en = _json.loads(en_text) if en_text else None
        except Exception:
            script_en = None
        return script_ja, script_en

    def get_pending_without_scripts(self, limit: int = 10) -> list[dict]:
        """status=pending かつ script_ja が未設定の偉人を返す（Cron台本生成用）"""
        data = self.query_figures({
            "and": [
                {"property": "status", "select": {"equals": "pending"}},
                {"property": "script_ja", "rich_text": {"is_empty": True}},
            ]
        })
        return [self._page_to_figure(p) for p in data[:limit]]

    def _ensure_script_properties(self):
        """script_ja / script_en プロパティがDBになければ追加する（初回のみ）"""
        if getattr(self, "_script_props_ensured", False):
            return
        try:
            self.session.patch(
                f"{NOTION_API_BASE}/databases/{self.database_id}",
                json={"properties": {
                    "script_ja": {"rich_text": {}},
                    "script_en": {"rich_text": {}},
                }},
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"script プロパティの追加に失敗（無視）: {e}")
        self._script_props_ensured = True

    def mark_producing(self, page_id: str):
        """制作中フラグを立てる（並列実行時の2重取得防止）"""
        self._patch(f"pages/{page_id}", {
            "properties": {
                "status": {"select": {"name": "producing"}},
            }
        })

    def mark_done(
        self,
        page_id: str,
        title_ja: str,
        title_en: str,
        jp_video_id: str = "",
        en_video_id: str = "",
    ):
        """制作完了・動画IDを記録する"""
        props = {
            "status": {"select": {"name": "done"}},
            "title_ja": {"rich_text": [{"text": {"content": title_ja}}]},
            "title_en": {"rich_text": [{"text": {"content": title_en}}]},
            "produced_at": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
        }
        if jp_video_id:
            props["jp_video_id"] = {"rich_text": [{"text": {"content": jp_video_id}}]}
        if en_video_id:
            props["en_video_id"] = {"rich_text": [{"text": {"content": en_video_id}}]}

        self._patch(f"pages/{page_id}", {"properties": props})
        logger.info(f"完了マーク: page_id={page_id}, jp={jp_video_id}, en={en_video_id}")

    def _ensure_error_log_property(self):
        """error_log プロパティが DB になければ追加する（初回のみ）"""
        if self._error_log_ensured:
            return
        try:
            self.session.patch(
                f"{NOTION_API_BASE}/databases/{self.database_id}",
                json={"properties": {"error_log": {"rich_text": {}}}},
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"error_log プロパティの追加に失敗（無視）: {e}")
        self._error_log_ensured = True

    def mark_error(self, page_id: str, error_msg: str):
        """エラーフラグを立てる（notes は上書きしない）"""
        self._ensure_error_log_property()
        self._patch(f"pages/{page_id}", {
            "properties": {
                "status": {"select": {"name": "error"}},
                "error_log": {"rich_text": [{"text": {"content": error_msg[:2000]}}]},
            }
        })

    # ─────────────────────────────────────────
    # ユーティリティ
    # ─────────────────────────────────────────

    @staticmethod
    def _get_prop_text(props: dict, key: str) -> str:
        prop = props.get(key, {})
        ptype = prop.get("type")
        if ptype == "title":
            items = prop.get("title", [])
        elif ptype == "rich_text":
            items = prop.get("rich_text", [])
        else:
            return ""
        return "".join(i.get("plain_text", "") for i in items)

    @staticmethod
    def _get_prop_select(props: dict, key: str) -> str:
        prop = props.get(key, {})
        sel = prop.get("select")
        return sel.get("name", "") if sel else ""

    @staticmethod
    def _get_prop_number(props: dict, key: str) -> Optional[int]:
        prop = props.get(key, {})
        return prop.get("number")

    def _page_to_figure(self, page: dict) -> dict:
        """Notionページを辞書形式に変換する"""
        props = page["properties"]
        return {
            "page_id": page["id"],
            "name_ja": self._get_prop_text(props, "Name"),
            "name_en": self._get_prop_text(props, "name_en"),
            "birth_year": self._get_prop_number(props, "birth_year"),
            "death_year": self._get_prop_number(props, "death_year"),
            "era": self._get_prop_select(props, "era"),
            "field": self._get_prop_select(props, "field"),
            "notes": self._get_prop_text(props, "notes"),
            "status": self._get_prop_select(props, "status"),
        }

    # ─────────────────────────────────────────
    # DB 初期セットアップ
    # ─────────────────────────────────────────

    def setup_database(self, parent_page_id: str) -> str:
        """
        偉人管理DBを新規作成してIDを返す。
        初回セットアップ時のみ使用。
        """
        payload = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": "偉人リスト"}}],
            "properties": {
                "Name": {"title": {}},
                "name_en": {"rich_text": {}},
                "birth_year": {"number": {"format": "number"}},
                "death_year": {"number": {"format": "number"}},
                "era": {"select": {"options": [
                    {"name": "飛鳥・奈良", "color": "yellow"},
                    {"name": "平安", "color": "purple"},
                    {"name": "鎌倉", "color": "brown"},
                    {"name": "室町・戦国", "color": "red"},
                    {"name": "江戸", "color": "blue"},
                    {"name": "明治", "color": "green"},
                    {"name": "大正・昭和", "color": "orange"},
                    {"name": "不明", "color": "gray"},
                ]}},
                "field": {"select": {"options": [
                    {"name": "科学者・発明家", "color": "blue"},
                    {"name": "女性の先駆者", "color": "pink"},
                    {"name": "芸術家・文化人", "color": "purple"},
                    {"name": "医師・思想家", "color": "green"},
                    {"name": "地方の英雄・反骨者", "color": "red"},
                    {"name": "外交官・先駆的外国人", "color": "orange"},
                    {"name": "その他", "color": "gray"},
                ]}},
                "notes": {"rich_text": {}},
                "status": {"select": {"options": [
                    {"name": "pending", "color": "gray"},
                    {"name": "producing", "color": "yellow"},
                    {"name": "done", "color": "green"},
                    {"name": "error", "color": "red"},
                ]}},
                "title_ja": {"rich_text": {}},
                "title_en": {"rich_text": {}},
                "jp_video_id": {"rich_text": {}},
                "en_video_id": {"rich_text": {}},
                "produced_at": {"date": {}},
            },
        }
        result = self._post("databases", payload)
        db_id = result["id"]
        logger.info(f"Notion DB 作成完了: {db_id}")
        return db_id


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    client = NotionFigureClient()
    pending = client.get_pending_figures(limit=3)
    for f in pending:
        print(f)
