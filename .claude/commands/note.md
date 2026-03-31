# note 記事投稿スキル

Notionの完成済み偉人データから note.com の記事を作成・投稿する。
画像はWikipedia肖像画のみ使用（動画フレームは使わない）。

## 実行手順

### 1. 投稿候補を取得

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
} for f in figures], ensure_ascii=False, indent=2))
EOF
```

### 2. 対象偉人の全データを取得

引数で名前が指定されていればその偉人、なければ先頭1件を使う。

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
    data = notion.query_figures({"property": "Name", "title": {"equals": target["name_ja"]}})
    if data:
        target["jp_video_id"] = notion._get_prop_text(data[0]["properties"], "jp_video_id")
    print(json.dumps(target, ensure_ascii=False, indent=2))
EOF
```

### 3. 使用する画像を確認

- **トップ画像**: `data/longform_output/【NAME】/images/wiki_00.jpg`（Wikipedia肖像画）
- **記事内サブ画像**: `data/longform_output/【NAME】/images/wiki_01.jpg`（あれば。墓所・関連場所など）

画像が存在するか確認：
```bash
ls data/longform_output/【NAME】/images/
```

### 4. note 記事を執筆（Claude 自身が書く・APIは不要）

`long_script_ja` を読み込み、以下の構成で日本語の note 記事を執筆する。

**記事構成：**
```
タイトル:
  長編タイトルをそのまま or「〇〇の知られざる真実｜偉人伝」形式

トップ画像: wiki_00.jpg（肖像画）

リード文（3〜4文）:
  最も驚くべきエピソードで興味を引く。

## 見出し（スクリプトのセクション見出しをそのまま使う）
本文（スクリプトの内容を読みやすく整形）

  ※ wiki_01.jpg は「晩年と遺産」セクション付近に挿入

---

▶ この偉人の動画はこちら

【ショート動画（60秒）】
https://youtube.com/shorts/【jp_video_id】

【長編動画】
https://youtu.be/【longform_video_id】

---

#偉人 #日本史 #歴史 #【name_ja】 #歴史解説
```

### 5. note.com に投稿（Chrome 操作）

note.com に Chrome でログイン済みであることを前提とする。
未ログインなら「ログインしてください」と伝えて停止。

```
1. navigate: https://note.com でログイン確認
2. 「投稿する」→「テキスト」をクリック
3. タイトルを入力
4. トップ画像をアップロード（wiki_00.jpg）
5. 本文を入力（各セクション）
6. wiki_01.jpg を晩年セクション付近に挿入
7. ハッシュタグを設定
8. 「公開設定」→「無料公開」→「投稿」
9. 投稿後の URL を取得
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

## オプション

- `/note --preview` → 投稿せず記事文だけ表示
- `/note 水野忠邦` → 指定偉人で実行

## 注意事項

- Chrome で note.com にログイン済みであること
- 画像は Wikipedia 肖像画（パブリックドメイン）のみ使用
- 1回の実行で投稿するのは1件のみ
