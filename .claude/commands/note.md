# note 記事生成スキル

Notionの `long_script_ja` から note.com 用の記事を生成して `note_article` に保存する。
投稿はユーザーが手動で行う（Chromeの自動操作は行わない）。

## 実行手順

### 1. 記事生成待ちの偉人を取得

```bash
cd /c/Users/kei20/mk_short && PYTHONIOENCODING=utf-8 python - <<'EOF'
import sys, json
sys.path.insert(0, "src")
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")
from notion_client import NotionFigureClient
notion = NotionFigureClient()
figures = notion.get_figures_ready_for_note_article(limit=5)
print(json.dumps([{
    "page_id": f["page_id"],
    "name_ja": f["name_ja"],
    "longform_video_id": f.get("longform_video_id",""),
    "jp_video_id": f.get("jp_video_id",""),
    "note_status": f.get("note_status",""),
} for f in figures], ensure_ascii=False, indent=2))
EOF
```

引数で名前が指定されていれば、対象をその偉人1件に絞る。

### 2. 対象偉人の long_script_ja を取得

```bash
cd /c/Users/kei20/mk_short && PYTHONIOENCODING=utf-8 python - <<'EOF'
import sys, json
sys.path.insert(0, "src")
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")
from notion_client import NotionFigureClient
notion = NotionFigureClient()
data = notion.query_figures({"property": "Name", "title": {"equals": "【NAME】"}})
if data:
    p = data[0]
    props = p["properties"]
    print(json.dumps({
        "page_id": p["id"],
        "name_ja": notion._get_prop_text(props, "Name"),
        "longform_video_id": notion._get_prop_text(props, "longform_video_id"),
        "jp_video_id": notion._get_prop_text(props, "jp_video_id"),
        "long_script_ja": notion._get_prop_text(props, "long_script_ja"),
    }, ensure_ascii=False, indent=2))
EOF
```

### 3. note 記事を執筆（Claude 自身が書く）

`long_script_ja` を読み込み、以下の構成で記事を生成する。

**記事構成：**
```
タイトル: （long_script_ja の「タイトル:」行をそのまま使う）

（リード文 3〜4文：最も驚くべきエピソードで冒頭を引く）

## （long_script_ja の各セクション見出しをそのまま使う）
（本文：スクリプト内容を読みやすく整形。話し言葉→書き言葉に変換）

---

▶ この偉人の動画はこちら

【ショート動画（60秒）】
https://youtube.com/shorts/【jp_video_id】

【長編動画（詳細解説）】
https://youtu.be/【longform_video_id】

---

#偉人 #日本史 #歴史 #【name_ja】 #歴史解説
```

**記事執筆の注意点：**
- long_script_ja の全セクションを使う（Hook・各章・Outro）
- 話し言葉を読み物として自然な書き言葉に変換する
- 見出しは `##` マークダウン形式で書く（note.comが対応）
- YouTubeリンクは埋め込みではなくURLテキストのまま（手動投稿時に埋め込みに変換）
- 文字数目安：2000〜3000文字

### 4. Notion の note_article に保存

```bash
cd /c/Users/kei20/mk_short && PYTHONIOENCODING=utf-8 python - <<'EOF'
import sys
sys.path.insert(0, "src")
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")
from notion_client import NotionFigureClient
notion = NotionFigureClient()
article = """【ここに生成した記事テキストを挿入】"""
notion.save_note_article("【PAGE_ID】", article)
print("保存完了")
EOF
```

保存後、Notionの `note_article` フィールドに記事が入り `note_status` が `article_ready` になる。

### 5. ユーザーへの案内

保存完了後、以下を伝える：

```
✅ note記事を生成してNotionに保存しました。

偉人名: 【name_ja】
Notionで「note_article」フィールドの内容をコピーしてnote.comに貼り付けてください。

投稿手順:
1. note.com でテキスト記事を新規作成
2. タイトルを入力
3. note_article の内容を本文に貼り付け
4. YouTubeリンクを埋め込みに変換（+ボタン→埋め込み）
5. トップ画像を設定（wiki_00.jpg）
6. ハッシュタグを公開設定で追加
7. 投稿後、URLをNotionの note_url に記録

画像パス: C:/Users/kei20/mk_short/data/longform_output/【name_ja】/images/wiki_00.jpg
```

## オプション

- `/note` → 未生成の先頭1件を処理
- `/note 水野忠邦` → 指定偉人で実行
- `/note --list` → 記事生成待ち一覧を表示

## note_url の記録（投稿後）

```bash
cd /c/Users/kei20/mk_short && PYTHONIOENCODING=utf-8 python - <<'EOF'
import sys
sys.path.insert(0, "src")
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")
from notion_client import NotionFigureClient
notion = NotionFigureClient()
notion.mark_note_posted("【PAGE_ID】", "【NOTE_URL】")
print("完了")
EOF
```
