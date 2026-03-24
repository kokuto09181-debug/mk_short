# YouTube Shorts 自動生成システム - セットアップガイド

## システム概要

毎日自動でYouTube Shortsを生成・投稿するシステムです。

### 月間コスト目安
| サービス | 用途 | コスト |
|---------|------|--------|
| Claude API (Haiku) | スクリプト生成 | ~$0.03/月 (30本) |
| edge-tts (Microsoft) | 音声合成 | **無料** |
| Pexels API | 背景素材 | **無料** |
| YouTube Data API | 動画投稿 | **無料** |
| GitHub Actions | 自動実行 | **無料** (2000分/月) |
| **合計** | | **~$0.03〜数百円/月** |

---

## セットアップ手順

### 1. 必要なAPIキーを取得

#### A. Anthropic API Key (Claude)
1. https://console.anthropic.com にアクセス
2. API Keys → Create Key
3. キーをコピー

#### B. Pexels API Key (背景素材・無料)
1. https://www.pexels.com/api/ にアクセス
2. アカウント作成 → APIキーを取得

#### C. YouTube Data API v3 + OAuth2
1. https://console.cloud.google.com にアクセス
2. 新しいプロジェクトを作成
3. 「YouTube Data API v3」を有効化
4. 認証情報 → OAuth 2.0 クライアントID → デスクトップアプリ
5. `client_secret.json` をダウンロード
6. **初回のみローカルで認証:**
   ```bash
   pip install -r requirements.txt
   cp /path/to/client_secret.json .
   python src/youtube_uploader.py --auth
   # ブラウザが開くのでGoogleアカウントでログイン
   # token.json が生成される
   ```

### 2. GitHub Secrets に設定

リポジトリの Settings → Secrets and variables → Actions → New repository secret

| Secret名 | 値 |
|---------|---|
| `ANTHROPIC_API_KEY` | AnthropicのAPIキー |
| `PEXELS_API_KEY` | PexelsのAPIキー |
| `YOUTUBE_TOKEN_JSON` | `token.json` の内容をそのまま貼り付け |

### 3. ローカルテスト（任意）

```bash
# 依存関係インストール
pip install -r requirements.txt

# ドライラン（アップロードなし）
export ANTHROPIC_API_KEY="your_key"
export PEXELS_API_KEY="your_key"
python src/pipeline.py --niche facts --language ja --dry-run

# 生成された動画を確認
ls output/
```

### 4. 自動実行の確認

GitHub Actions タブから:
- **自動**: 毎日JST 10:00 に実行
- **手動**: Actions → "Daily YouTube Shorts Generator" → Run workflow

---

## ディレクトリ構造

```
mk_short/
├── src/
│   ├── content_generator.py  # Claude APIでスクリプト生成
│   ├── tts_generator.py      # edge-ttsで音声生成
│   ├── background_fetcher.py # Pexelsから背景素材取得
│   ├── video_assembler.py    # MoviePyで動画合成
│   ├── youtube_uploader.py   # YouTube APIで投稿
│   └── pipeline.py           # 全体パイプライン
├── templates/
│   └── topics.json           # ニッチ・テーマ設定
├── assets/
│   └── fonts/                # カスタムフォント（任意）
├── output/                   # 生成された動画（gitignore済み）
├── .github/workflows/
│   └── daily_short.yml       # GitHub Actions設定
├── config.py                 # 設定ファイル
├── main.py                   # エントリーポイント
└── requirements.txt
```

---

## カスタマイズ

### ニッチを変更
`config.py` または GitHub Secret `CONTENT_NICHE` で設定:
- `facts` - 面白い雑学（デフォルト）
- `motivation` - 名言・モチベーション
- `money` - お金の知識・節約

### 新しいテーマを追加
`templates/topics.json` に追加

### 投稿頻度を変更
`.github/workflows/daily_short.yml` の `cron` を編集:
- 毎日1本: `"0 1 * * *"`
- 毎日2本: 2つのcronジョブを設定
- 週5本: `"0 1 * * 1-5"`

---

## 収益化のポイント

### YouTube Shortsの収益化条件（2024年〜）
- チャンネル登録者数: **500人以上**
- 過去90日間のShortsの視聴回数: **300万回以上**
OR
- チャンネル登録者数: **1,000人以上**
- 過去12ヶ月間の視聴時間: **4,000時間以上**

### 高収益ニッチ（CPM目安）
1. 💰 **お金・投資** - 高CPM ($5-15)
2. 🧠 **教育・学習** - 中〜高CPM ($3-10)
3. 😄 **雑学・エンタメ** - 低〜中CPM ($1-5)

### SEO最適化のコツ
- タイトルに「なぜ」「知らない」「衝撃」などを使う
- 最初の3秒が最重要（フック）
- ハッシュタグ #shorts を必ず含める
- 毎日投稿で継続性を保つ
