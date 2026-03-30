"""
長編動画 脚本生成スクリプト
Notion の research_data を読み込み、Claude Sonnet API で
8〜12分の長編動画脚本（日本語のみ）を生成して Notion に保存する。

脚本構成（8セクション）:
  1. Hook        : 掴み（視聴者の興味を引くエピソード）
  2. 時代背景    : 偉人が生きた時代の状況
  3. 生い立ち    : 幼少期・成長環境
  4. 転機        : 人生を変えた出来事
  5. 最大業績    : 最も重要な功績
  6. 苦難        : 乗り越えた困難・逆境
  7. 晩年        : 晩年の様子・影響
  8. 締め        : 現代へのメッセージ・まとめ

出力形式: JSONではなくプレーンテキスト（段落区切りでセクション分け）

使用方法:
  python scripts/generate_long_script.py           # 未生成の全偉人を処理
  python scripts/generate_long_script.py --limit 3 # 最大3件処理
  python scripts/generate_long_script.py --all     # 全偉人を再生成（上書き）
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from notion_client import NotionFigureClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

MODEL_HAIKU = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-6"
MAX_TOKENS = 10000

TARGET_CHARS_JA = 3200  # 日本語: 約10〜11分（edge-tts実測 ~290文字/分）


JA_PROMPT_TEMPLATE = """あなたは日本の歴史偉人を紹介するYouTubeチャンネルのプロ脚本家です。
以下の情報を基に、8〜12分の長編YouTube動画用脚本を日本語で作成してください。

【偉人情報】
{research_data}

【脚本の要件】
- 合計ナレーション文字数: {target_chars}文字以上（厳守。不足する場合は各セクションを肉付けして増やすこと）
- 各セクション最低350文字以上（短すぎるセクションは禁止）
- 視聴者層: 歴史に興味のある20〜50代の日本人
- トーン: 親しみやすく情熱的。ドラマチックな語り口
- 各セクションに見出しタイトルを付ける
- 具体的なエピソード・逸話を豊富に入れる（情景描写・会話調の表現も活用）
- 現代への示唆・教訓で締める
- Hook（掴み）セクション: 短編動画の単独予告として機能するよう、衝撃的な問いかけで始めて視聴者を引き込む構成にする

【必須セクション構成】
1. Hook（掴み）: 最も驚くべきエピソードから始め、「なぜこの人物がここまで〜したのか？」という問いを立てる
2. 時代背景: {name_ja}が生きた時代の社会状況・時代の空気感
3. 生い立ち: 幼少期・家庭環境・若い頃の原体験
4. 転機: 人生を180度変えた出来事・決断
5. 最大業績: 歴史に名を刻んだ功績・発見・作品
6. 苦難と逆境: 乗り越えた困難・批判・失敗
7. 晩年と遺産: 晩年の様子と後世への影響
8. 現代へのメッセージ: 今を生きる私たちへの教訓・まとめ

【出力形式】
以下のプレーンテキスト形式で出力してください。JSONは不要です。
セクションは「==============================」で区切ってください。

タイトル: [動画タイトル（30文字以内）]

説明文: [動画説明文（200文字以内）]

タグ: [カンマ区切りタグ（例: 偉人,日本史,歴史,江戸時代）]

==============================

【Hook】掴み

[ナレーション本文。改行・段落を使って自然な語り口で]

==============================

【時代背景】

[ナレーション本文]

==============================

【生い立ち】

[ナレーション本文]

==============================

【転機】

[ナレーション本文]

==============================

【最大業績】

[ナレーション本文]

==============================

【苦難と逆境】

[ナレーション本文]

==============================

【晩年と遺産】

[ナレーション本文]

==============================

【現代へのメッセージ】

[ナレーション本文]
"""


def generate_script(client: anthropic.Anthropic, figure: dict, model: str = MODEL_HAIKU) -> str:
    """Claude Sonnet で日本語脚本を生成してプレーンテキストを返す"""
    research_data = figure.get("research_data", "")
    if not research_data:
        raise ValueError(f"research_data が空: {figure.get('name_ja')}")

    prompt = JA_PROMPT_TEMPLATE.format(
        research_data=research_data,
        name_ja=figure.get("name_ja", "この人物"),
        target_chars=TARGET_CHARS_JA,
    )

    message = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text.strip()
    logger.info(
        f"  トークン使用: input={message.usage.input_tokens}, "
        f"output={message.usage.output_tokens}"
    )
    return text


def run(limit: int = 20, force_all: bool = False, model: str = MODEL_HAIKU):
    """メイン処理"""
    notion = NotionFigureClient()
    ai_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    logger.info(f"使用モデル: {model}")

    notion.ensure_longform_properties()

    if force_all:
        pages = notion.query_figures()
        figures = [notion._page_to_figure(p) for p in pages]
        figures = [f for f in figures if f.get("research_data")]
        logger.info(f"全偉人対象（research_data あり）: {len(figures)} 件")
    else:
        figures = notion.get_figures_without_long_scripts(limit=limit)
        figures_with_data = [f for f in figures if f.get("research_data")]
        skipped = len(figures) - len(figures_with_data)
        if skipped:
            logger.warning(
                f"{skipped} 件は research_data 未収集のためスキップ"
                "（先に gather_figure_info.py を実行してください）"
            )
        figures = figures_with_data

    if not figures:
        logger.info("脚本生成対象の偉人がいません。完了。")
        return

    success = 0
    for i, figure in enumerate(figures, 1):
        name_ja = figure.get("name_ja", "不明")
        page_id = figure["page_id"]
        logger.info(f"[{i}/{len(figures)}] 脚本生成中: {name_ja}")

        try:
            script_text = generate_script(ai_client, figure, model=model)
            char_count = len(script_text)
            logger.info(f"  → 脚本完成: {char_count}文字")

            notion.save_long_script_ja(page_id=page_id, text=script_text)
            success += 1
            logger.info(f"  → 保存完了: {name_ja}")

        except Exception as e:
            logger.error(f"  → エラー: {name_ja}: {e}")

        if i < len(figures):
            time.sleep(3)

    logger.info(f"=== 完了: {success}/{len(figures)} 件の脚本を生成・保存 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Claude による長編動画脚本生成（日本語）")
    parser.add_argument("--limit", type=int, default=20, help="最大処理件数（デフォルト: 20）")
    parser.add_argument("--all", dest="force_all", action="store_true",
                        help="全偉人を強制再生成（デフォルト: 未生成のみ）")
    parser.add_argument("--model", type=str, default=MODEL_HAIKU,
                        choices=[MODEL_HAIKU, MODEL_SONNET],
                        help=f"使用モデル（デフォルト: {MODEL_HAIKU}）")
    args = parser.parse_args()

    run(limit=args.limit, force_all=args.force_all, model=args.model)
