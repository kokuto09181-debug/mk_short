# note 記事投稿スキル

Notionの完成済み偉人データから note.com の記事を作成・投稿する。

## 実行手順

### 1. 投稿候補を取得

以下のPythonスクリプトをBashツールで実行して、note未投稿の偉人一覧を取得する：

```bash
cd /c/Users/kei20/mk_short && PYTHONIOENCODING=utf-8 python - <<'EOF'
import sys, json
sys.path.insert(0, "src")
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")
from notion_client import NotionFigureClient
notion = NotionFigureClient()
figures = notion.get_figures_ready_for_note(limit=10)
print(json.dumps([{
    "page_id": f["page_id"],
    "name_ja": f["name_ja"],
    "longform_video_id": f.get("longform_video_id",""),
    "jp_video_id": f.get("jp_video_id",""),
    "long_script_ja": f.get("long_script_ja","")[:200]
} for f in figures], ensure_ascii=False, indent=2))
EOF
```

### 2. 投稿する偉人を選ぶ

- 引数で名前が指定されていればその偉人を使う（例: `/note 水野忠邦`）
- 指定なしの場合は候補の先頭1件を使う
- `longform_video_id` があれば長編リンクを記事に含める
- `jp_video_id` があればショートリンクを記事に含める

### 3. long_script_ja を全文取得

```bash
cd /c/Users/kei20/mk_short && PYTHONIOENCODING=utf-8 python - <<'EOF'
import sys, json
sys.path.insert(0, "src")
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")
from notion_client import NotionFigureClient
notion = NotionFigureClient()
figures = notion.get_figures_ready_for_note(limit=50)
target = next((f for f in figures if f["name_ja"] == "【NAME】"), figures[0] if figures else None)
if target:
    print(json.dumps(target, ensure_ascii=False, indent=2))
EOF
```

### 4. note 記事を作成（Claude 自身が執筆）

`long_script_ja` の内容を読み、以下の構成で**日本語の note 記事**を書く。
APIは使わず、Claude Code 自身がそのまま記事テキストを生成する。

**記事構成：**
```
タイトル: （長編タイトルをそのまま、または「〇〇の知られざる真実｜偉人伝」形式）

リード文（3〜4文）:
  この偉人の最も驚くべきエピソードを1つ挙げて興味を引く。
  「この記事ではXXXについて詳しく解説します」で締める。

## 見出し1（長編の各セクション見出しをそのまま使う）
セクション本文をそのまま引用 or 読みやすく整形

## 見出し2
...（全セクション分）

---

▶ この偉人の動画はこちら

【ショート動画（60秒）】
https://youtube.com/shorts/【jp_video_id】

【長編動画（〇分）】
https://youtu.be/【longform_video_id】

---

#偉人 #日本史 #歴史 #【名前】 #歴史解説
```

### 5. note.com に投稿（Chrome 操作）

Chrome MCP ツールを使って note.com に記事を投稿する。

```
1. mcp__Claude_in_Chrome__navigate で https://note.com にアクセス
2. ログイン済みかを確認（未ログインなら「ログインしてください」とユーザーに伝えて停止）
3. 「投稿する」ボタンをクリック → 「テキスト」を選択
4. タイトルを入力
5. 本文を入力（mcp__Claude_in_Chrome__form_input を使用）
6. ハッシュタグを設定
7. 「公開」→ 「無料公開」で投稿
8. 投稿後の URL を取得
```

### 6. Notion に投稿済みを記録

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

## 注意事項

- note.com に Chrome でログインしていることが前提
- 投稿前に記事内容をユーザーに確認してもよい（`/note --preview` の場合は投稿せず記事文のみ表示）
- 1回の実行で投稿するのは1件のみ（複数投稿は複数回実行）
- エラーが発生したら Notion の note_status は変更しない
