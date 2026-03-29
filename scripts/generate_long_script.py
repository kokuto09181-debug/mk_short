"""
長編動画 脚本生成スクリプト
Notion の research_data を読み込み、Claude Haiku API で
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

使用方法:
  python scripts/generate_long_script.py           # 未生成の全偉人を処理
  python scripts/generate_long_script.py --limit 3 # 最大3件処理
  python scripts/generate_long_script.py --all     # 全偉人を再生成（上書き）
"""

import argparse
import json
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

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 4096

TARGET_CHARS_JA = 3500  # 日本語: 約14分相当（ゆっくり読み）


JA_PROMPT_TEMPLATE = """
あなたは日本の歴史偉人を紹介するYouTubeチャンネルのプロ脚本家です。
以下の情報を基に、8〜12分の長編YouTube動画用脚本を日本語で作成してください。

【偉人情報】
{research_data}

【脚本の要件】
- 合計ナレーション文字数: {target_chars}文字前後（多少の誤差OK）
- 視聴者層: 歴史に興味のある20〜50代の日本人
- トーン: 親しみやすく情熱的。ドラマチックな語り口
- 各セクションに見出しタイトルを付ける
- 具体的なエピソード・逸話を豊富に入れる
- 現代への示唆・教訓で締める

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
以下のJSON形式で出力してください（日本語）:

{{
  "title": "動画タイトル（30文字以内、魅力的なもの）",
  "description": "動画説明文（200文字以内）",
  "tags": ["タグ1", "タグ2", ...],
  "sections": [
    {{
      "section_id": 1,
      "section_title": "【Hook】〜〜〜",
      "narration": "ナレーションテキスト（このセクションの読み上げ文）",
      "visual_note": "映像演出のヒント（どんな画像・テキストアニメを使うか）"
    }},
    ...（8セクション分）
  ],
  "total_chars": 合計ナレーション文字数（数値）
}}
"""


def generate_script(client: anthropic.Anthropic, figure: dict) -> dict:
    """Claude Haiku で日本語脚本を生成して dict を返す"""
    research_data = figure.get("research_data", "")
    if not research_data:
        raise ValueError(f"research_data が空: {figure.get('name_ja')}")

    prompt = JA_PROMPT_TEMPLATE.format(
        research_data=research_data,
        name_ja=figure.get("name_ja", "この人物"),
        target_chars=TARGET_CHARS_JA,
    )

    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()

    # JSONブロックを抽出
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    script = json.loads(raw)

    # メタ情報を付加
    script["language"] = "ja"
    script["figure_name_ja"] = figure.get("name_ja", "")
    script["figure_name_en"] = figure.get("name_en", "")
    script["figure_era"] = figure.get("era", "")
    script["figure_field"] = figure.get("field", "")

    return script


def run(limit: int = 20, force_all: bool = False):
    """メイン処理"""
    notion = NotionFigureClient()
    ai_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Notionに新フィールドが存在しない場合は追加
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
            logger.warning(f"{skipped} 件は research_data 未収集のためスキップ（先に gather_figure_info.py を実行してください）")
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
            script_ja = generate_script(ai_client, figure)
            ja_chars = script_ja.get("total_chars", 0)
            logger.info(f"  → 脚本完成: {ja_chars}文字 / {len(script_ja.get('sections', []))}セクション")

            notion.save_long_script_ja(
                page_id=page_id,
                long_script_ja_json=json.dumps(script_ja, ensure_ascii=False),
            )
            success += 1
            logger.info(f"  → 保存完了: {name_ja}")

        except json.JSONDecodeError as e:
            logger.error(f"  → JSON解析エラー: {name_ja}: {e}")
        except Exception as e:
            logger.error(f"  → エラー: {name_ja}: {e}")

        if i < len(figures):
            time.sleep(3)

    logger.info(f"=== 完了: {success}/{len(figures)} 件の脚本を生成・保存 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Claude Haiku による長編動画脚本生成（日本語）")
    parser.add_argument("--limit", type=int, default=20, help="最大処理件数（デフォルト: 20）")
    parser.add_argument("--all", dest="force_all", action="store_true",
                        help="全偉人を強制再生成（デフォルト: 未生成のみ）")
    args = parser.parse_args()

    run(limit=args.limit, force_all=args.force_all)
