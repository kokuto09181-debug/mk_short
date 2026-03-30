"""
長編動画 スタンドアロンテスト
Notion不要で1名分の長編動画制作フローを完走する。

実行方法:
  python scripts/test_longform.py
  python scripts/test_longform.py --name "北里柴三郎" --name-en "Kitasato Shibasaburo"
  python scripts/test_longform.py --skip-video  # 動画生成をスキップ
"""

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────
# ステップ1: Wikipedia 情報収集
# ─────────────────────────────

import requests

WIKI_JA_API = "https://ja.wikipedia.org/w/api.php"
WIKI_EN_API = "https://en.wikipedia.org/w/api.php"
JA_EXTRACT_MAX = 6000
EN_EXTRACT_MAX = 2000


WIKI_HEADERS = {"User-Agent": "mk_short_longform_test/1.0 (educational; contact via github)"}


def fetch_wikipedia_extract(title: str, lang: str = "ja") -> str:
    api_url = WIKI_JA_API if lang == "ja" else WIKI_EN_API
    params = {
        "action": "query",
        "titles": title,
        "prop": "extracts",
        "explaintext": True,
        "exsectionformat": "plain",
        "format": "json",
        "redirects": True,
    }
    try:
        resp = requests.get(api_url, params=params, headers=WIKI_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        for page_data in pages.values():
            if "missing" in page_data:
                return ""
            return page_data.get("extract", "")
    except Exception as e:
        logger.warning(f"Wikipedia取得失敗 [{lang}] {title}: {e}")
        return ""


def gather_research(name_ja: str, name_en: str, era: str = "", field: str = "") -> str:
    logger.info(f"[STEP 1] Wikipedia情報収集: {name_ja}")
    lines = [f"【偉人名】{name_ja}（{name_en}）", f"【時代】{era}　【分野】{field}", ""]

    ja_extract = fetch_wikipedia_extract(name_ja, lang="ja")
    if ja_extract:
        lines.append("=== Wikipedia（日本語）===")
        lines.append(ja_extract[:JA_EXTRACT_MAX])
        if len(ja_extract) > JA_EXTRACT_MAX:
            lines.append("（以下省略）")
        lines.append("")
    else:
        lines.append("=== Wikipedia（日本語）: 記事なし ===\n")
        logger.warning(f"日本語Wikipedia未発見: {name_ja}")

    time.sleep(1.5)

    en_title = name_en if name_en else name_ja
    en_extract = fetch_wikipedia_extract(en_title, lang="en")
    if en_extract:
        lines.append("=== Wikipedia (English) ===")
        lines.append(en_extract[:EN_EXTRACT_MAX])
        if len(en_extract) > EN_EXTRACT_MAX:
            lines.append("(truncated)")
        lines.append("")
    else:
        lines.append("=== Wikipedia (English): No article found ===\n")

    research = "\n".join(lines)
    logger.info(f"[STEP 1] 収集完了: {len(research)}文字")
    return research


# ─────────────────────────────
# ステップ2: 長編脚本生成
# ─────────────────────────────

import anthropic

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 8000
TARGET_CHARS_JA = 2400  # 日本語約8〜10分（haiku 8192トークン上限に収める）

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


def generate_script(research_data: str, name_ja: str, name_en: str) -> dict:
    logger.info(f"[STEP 2] 脚本生成中: {name_ja}")
    ai = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = JA_PROMPT_TEMPLATE.format(
        research_data=research_data,
        name_ja=name_ja,
        target_chars=TARGET_CHARS_JA,
    )

    message = ai.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    script = json.loads(raw)
    script["figure_name_ja"] = name_ja
    script["figure_name_en"] = name_en
    logger.info(f"[STEP 2] 脚本完成: {script.get('total_chars', '?')}文字 / {len(script.get('sections', []))}セクション")
    return script


# ─────────────────────────────
# ステップ3: 動画生成（Shorts用VideoCreatorを流用）
# ─────────────────────────────

def create_long_video(script: dict, research: str, output_dir: str) -> str:
    """
    長編動画を生成する。
    各セクションごとにTTSナレーションを生成し、動画クリップを結合する。
    """
    logger.info("[STEP 3] 動画生成開始")
    from tts_generator import TTSGenerator
    from video_creator import VideoCreator
    try:
        from moviepy.editor import VideoFileClip, concatenate_videoclips
    except ImportError:
        from moviepy.video.io.VideoFileClip import VideoFileClip
        from moviepy.editor import concatenate_videoclips

    os.makedirs(output_dir, exist_ok=True)
    tts = TTSGenerator()
    vc = VideoCreator()

    sections = script.get("sections", [])
    clip_paths = []

    for i, section in enumerate(sections):
        narration = section.get("narration", "")
        if not narration:
            continue

        sec_dir = os.path.join(output_dir, f"section_{i+1:02d}")
        os.makedirs(sec_dir, exist_ok=True)

        # TTS 音声生成
        audio_path, duration = tts.generate_with_speed(narration, sec_dir)
        logger.info(f"  セクション{i+1}/{len(sections)}: {section.get('section_title', '')[:20]} ({duration:.1f}秒)")

        # Shorts用VideoCreatorのcreate_video呼び出し（各セクション1クリップ）
        section_script = {
            "title": section.get("section_title", ""),
            "body": narration,
            "language": "ja",
            "figure_name_ja": script.get("figure_name_ja", ""),
            "figure_name_en": script.get("figure_name_en", ""),
            "figure_era": script.get("figure_era", ""),
            "figure_field": script.get("figure_field", ""),
            "search_keywords_en": ["Japan", "history"],
        }
        clip_path = os.path.join(sec_dir, "clip.mp4")
        vc.create_video(
            script=section_script,
            audio_path=audio_path,
            image_paths=[],
            output_path=clip_path,
            narration=narration,
        )
        clip_paths.append(clip_path)

    # 全クリップを結合
    logger.info(f"[STEP 3] {len(clip_paths)}クリップを結合中...")
    clips = [VideoFileClip(p) for p in clip_paths]
    final = concatenate_videoclips(clips, method="compose")
    output_path = os.path.join(output_dir, "longform_output.mp4")
    final.write_videofile(output_path, fps=30, codec="libx264", audio_codec="aac", logger=None)
    for c in clips:
        c.close()
    final.close()

    logger.info(f"[STEP 3] 動画完成: {output_path} ({final.duration:.1f}秒 / {final.duration/60:.1f}分)")
    return output_path


# ─────────────────────────────
# メイン
# ─────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="長編動画 スタンドアロンテスト")
    parser.add_argument("--name", default="北里柴三郎", help="偉人の日本語名")
    parser.add_argument("--name-en", default="Kitasato Shibasaburo", help="偉人の英語名")
    parser.add_argument("--era", default="明治", help="時代")
    parser.add_argument("--field", default="科学者・発明家", help="分野")
    parser.add_argument("--output-dir", default="data/longform_test", help="出力ディレクトリ")
    parser.add_argument("--skip-video", action="store_true", help="動画生成をスキップ（脚本のみ）")
    args = parser.parse_args()

    output_dir = Path(__file__).parent.parent / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # STEP 1: Wikipedia情報収集
    research = gather_research(args.name, args.name_en, args.era, args.field)
    research_path = output_dir / "research_data.txt"
    research_path.write_text(research, encoding="utf-8")
    logger.info(f"research_data 保存: {research_path}")

    # STEP 2: 脚本生成
    script = generate_script(research, args.name, args.name_en)
    script_path = output_dir / "long_script_ja.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"脚本 保存: {script_path}")

    # サマリー表示
    print("\n" + "="*50)
    print(f"タイトル: {script.get('title')}")
    print(f"説明文: {script.get('description', '')[:80]}")
    print(f"合計文字数: {script.get('total_chars')} 文字")
    print(f"セクション数: {len(script.get('sections', []))}")
    for s in script.get("sections", []):
        chars = len(s.get("narration", ""))
        print(f"  [{s['section_id']}] {s['section_title'][:30]} ({chars}文字)")
    print("="*50 + "\n")

    # STEP 3: 動画生成
    if not args.skip_video:
        video_dir = str(output_dir / "video")
        video_path = create_long_video(script, research, video_dir)
        print(f"動画出力: {video_path}")
    else:
        logger.info("[STEP 3] 動画生成スキップ")

    print(f"\n出力先: {output_dir}")


if __name__ == "__main__":
    main()
