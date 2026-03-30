# 拡散戦略プラン

## 方針

Shorts 動画を生成したタイミングで、**同じ動画を複数媒体へ同時投稿**する。  
どの媒体で反応が出るかを観測し、顧客がどこにいるかを把握する。

- ターゲット：歴史に興味があるライト層（50代前後）
- コンテンツ：Wikipedia ベースの「知らなかった！」系ショート動画
- ❌ マニア向けコミュニティ（5ch・専門メディア等）には投げない

---

## 同時投稿先

| # | プラットフォーム | 形式 | 理由 |
|---|----------------|------|------|
| 1 | **YouTube Shorts** | 動画 | 現行稼働中 |
| 2 | **X (Twitter)** | 動画 + テキスト | 歴史好きコミュニティが活発。RTで拡散 |
| 3 | **LINE VOOM** | 動画 | 50代以上への到達力が最高水準 |
| 4 | **Instagram リール** | 動画 | 40〜50代女性リーチ |
| 5 | **Facebook リール** | 動画 | 日本の50代以上が最も使うSNS |
| 6 | **ニコニコ動画** | 動画 | 30〜50代。歴史タグの一般視聴者層あり |

---

## 投稿タイミング

YouTube アップロード完了と同時に全媒体へ投稿。  
現在のスケジュール（07:00 / 12:30 / 20:00 JST）に準拠。

---

## 実装方針

`pipeline.py` の YouTube アップロード直後に各投稿処理を追加する。

```python
video_id = uploader.upload(...)          # YouTube（既存）
x_poster.post(video_path, script)        # X
line_voom_poster.post(video_path)        # LINE VOOM
meta_poster.post(video_path, script)     # Instagram / Facebook
niconico_poster.post(video_path, script) # ニコニコ動画
```

各 poster はアップロード失敗しても pipeline 全体を止めない（try/except で握り潰す）。

---

## 実装ロードマップ

| Phase | 対象 | 工数 | API |
|-------|------|------|-----|
| 1 | X (Twitter) | 小 | Twitter API v2 |
| 2 | Instagram / Facebook | 中 | Meta Graph API |
| 3 | LINE VOOM | 中 | LINE Messaging API |
| 4 | ニコニコ動画 | 中 | ニコニコ投稿 API |

---

## 効果測定

2〜4週間後に各媒体の反応を比較し、反応の薄い媒体は停止する。

| 指標 | 確認内容 |
|------|---------|
| インプレッション | どの媒体で最も表示されたか |
| エンゲージメント率 | いいね・コメント・保存率 |
| YouTube 流入元 | どの媒体から YouTube に来ているか |

---

*作成: 2026-03-30*
