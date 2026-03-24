# セットアップガイド

## 必要なアカウント・API

| サービス | 用途 | 費用 |
|---|---|---|
| GitHub | Actions での自動実行 | 無料 |
| Anthropic | Claude Haiku で脚本生成 | ~$1-3/月 |
| Notion | 偉人リスト管理 | 無料 |
| Pexels | 背景画像取得 | 無料 |
| Google Cloud | YouTube Data API v3 | 無料枠内 |

---

## Step 1: Notion セットアップ

### 1-1. Notion Integration 作成
1. https://www.notion.so/my-integrations にアクセス
2. 「New integration」→ 名前を `youtube-shorts-bot` などに
3. 表示された **Internal Integration Token** をコピー
4. これが `NOTION_TOKEN`

### 1-2. Notion ページ作成
1. Notion で空のページを作成
2. ページを開いて URL から ID を取得
   - `https://notion.so/xxxxxxxxxx?v=...` の `xxxxxxxxxx` 部分
3. ページ右上「...」→「Add connections」→ 作成した Integration を追加

### 1-3. Notion DB 自動作成
GitHub Actionsの `Seed Notion Database` ワークフローを実行:
- `setup_db: true`
- `parent_page_id`: 上記のページID

出力された `NOTION_DATABASE_ID` をコピー。

---

## Step 2: YouTube API セットアップ

### 2-1. Google Cloud プロジェクト作成
1. https://console.cloud.google.com/ でプロジェクト作成
2. YouTube Data API v3 を有効化
3. 認証情報 → OAuth 2.0 クライアント ID 作成（デスクトップアプリ）
4. JSON をダウンロード

### 2-2. 日本語チャンネル用トークン取得（ローカルで実行）
```bash
export YOUTUBE_CLIENT_SECRET_JSON_JP='JSONの内容をここに貼る'
python src/uploader.py  # ブラウザで認証が開く
# .youtube_token.japanese.json が生成される
cat .youtube_token.japanese.json  # これが YOUTUBE_TOKEN_JSON_JP
```

### 2-3. 英語チャンネル用トークン取得
```bash
export YOUTUBE_CLIENT_SECRET_JSON_EN='JSONの内容をここに貼る'
# uploader.py の channel を "english" に変えて実行
# .youtube_token.english.json が生成される → YOUTUBE_TOKEN_JSON_EN
```

---

## Step 3: Pexels API キー取得
1. https://www.pexels.com/api/ でアカウント作成
2. API キーを取得（無料、月25,000リクエスト）
3. これが `PEXELS_API_KEY`

---

## Step 4: GitHub Secrets 設定

リポジトリの Settings → Secrets and variables → Actions に追加:

| Secret 名 | 値 |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API キー |
| `NOTION_TOKEN` | Notion Integration Token |
| `NOTION_DATABASE_ID` | Notion DB ID |
| `PEXELS_API_KEY` | Pexels API キー |
| `YOUTUBE_TOKEN_JSON_JP` | 日本語チャンネルのOAuth2トークンJSON |
| `YOUTUBE_TOKEN_JSON_EN` | 英語チャンネルのOAuth2トークンJSON |

---

## Step 5: 偉人シードデータ投入

GitHub Actions → `Seed Notion Database` → Run workflow
- `setup_db: false`（DBはStep1で作成済み）

`data/figures_seed.json` の25人がNotionに登録される。

---

## Step 6: 動作確認（dry run）

GitHub Actions → `Daily Shorts Generation` → Run workflow
- `dry_run: true`
- `count: 1`

ログを確認して動画生成まで成功していれば OK。

---

## Step 7: 本番稼働

`dry_run: false` で実行 → YouTube に投稿される。

以後、毎日自動で以下の時間に投稿:
- 07:00 JST
- 12:30 JST
- 20:00 JST

---

## Notion DB の見方

| status | 意味 |
|---|---|
| `pending` | 未制作（キュー待ち） |
| `producing` | 制作中（実行中）|
| `done` | 投稿済み |
| `error` | エラー発生（notesにエラー内容） |

---

## コスト試算（月間）

| 項目 | 計算 | 金額 |
|---|---|---|
| Claude Haiku | 6動画/日 × 30日 × 約$0.003 | ~$0.54 |
| GitHub Actions | ~80分/日 × 30日 = 2400分 ※ | 無料〜$4 |
| Pexels | 6リクエスト/日 × 30日 = 180 | 無料 |
| YouTube API | 無料枠内 | 無料 |
| **合計** | | **~$1-5/月** |

※ GitHub Actions無料枠2,000分を超えると課金。超える場合は1本/日に減らすか有料プランへ。

---

## ローカル実行

```bash
pip install -r requirements.txt

# 偉人シード投入
python src/pipeline.py seed

# dry run（1本）
python src/pipeline.py run --dry-run --count 1

# 本番実行
python src/pipeline.py run --count 3
```
