"""
コンテンツ生成モジュール
Claude API (Haiku) を使ってスクリプトを生成する
"""
import json
import random
import re
import anthropic
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config


def load_templates() -> dict:
    templates_path = Path(__file__).parent.parent / "templates" / "topics.json"
    with open(templates_path, "r", encoding="utf-8") as f:
        return json.load(f)


def generate_script(niche: str = None, language: str = None) -> dict:
    """
    Claude APIを使ってショート動画スクリプトを生成する

    Returns:
        {
            "title": str,
            "hook": str,
            "body": str,
            "cta": str,
            "tags": list[str],
            "thumbnail_text": str,
            "background_keyword": str,
            "full_script": str,  # TTS用の全文
        }
    """
    niche = niche or config.CONTENT_NICHE
    language = language or config.CONTENT_LANGUAGE

    templates = load_templates()
    niche_data = templates["niches"].get(niche, templates["niches"]["facts"])

    # 言語設定を取得（フォールバックあり）
    if language in niche_data:
        lang_data = niche_data[language]
    else:
        lang_data = niche_data.get("ja", list(niche_data.values())[0])

    theme = random.choice(lang_data["themes"])
    prompt = lang_data["prompt_template"].format(theme=theme)

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    message = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1024,
        system=lang_data["system_prompt"],
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = message.content[0].text
    return _parse_script(raw_text, language)


def _parse_script(raw_text: str, language: str = "ja") -> dict:
    """生成されたテキストをパースして構造化データに変換"""
    fields = {
        "title": "",
        "hook": "",
        "body": "",
        "cta": "",
        "tags": [],
        "thumbnail_text": "",
        "background_keyword": "nature",
    }

    lines = raw_text.strip().split("\n")
    current_field = None
    current_content = []

    field_map = {
        "TITLE:": "title",
        "HOOK:": "hook",
        "BODY:": "body",
        "CTA:": "cta",
        "TAGS:": "tags",
        "THUMBNAIL_TEXT:": "thumbnail_text",
        "BACKGROUND_KEYWORD:": "background_keyword",
    }

    for line in lines:
        line = line.strip()
        if not line:
            continue

        matched = False
        for key, field in field_map.items():
            if line.upper().startswith(key):
                if current_field:
                    fields[current_field] = " ".join(current_content).strip()
                current_field = field
                current_content = [line[len(key):].strip()]
                matched = True
                break

        if not matched and current_field:
            current_content.append(line)

    if current_field:
        fields[current_field] = " ".join(current_content).strip()

    # タグをリストに変換
    if isinstance(fields["tags"], str):
        tags_raw = fields["tags"]
        fields["tags"] = re.findall(r"#\w+", tags_raw)
        if not fields["tags"]:
            fields["tags"] = [w for w in tags_raw.split() if w.startswith("#")]

    # TTS用の全文を組み立て
    fields["full_script"] = _build_tts_script(fields, language)

    return fields


def _build_tts_script(fields: dict, language: str = "ja") -> str:
    """TTS用のスクリプト全文を組み立て"""
    parts = []
    if fields.get("hook"):
        parts.append(fields["hook"])
    if fields.get("body"):
        parts.append(fields["body"])
    if fields.get("cta"):
        parts.append(fields["cta"])
    return " ".join(parts)


if __name__ == "__main__":
    # テスト実行
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY が設定されていません。モックデータを使用します。")
        mock_result = {
            "title": "【衝撃】タコには心臓が3つある！",
            "hook": "タコには心臓が何個あるか知っていますか？",
            "body": "実はタコには3つの心臓があります。2つのエラ心臓と1つの体心臓です。しかもタコの血液は青色！ヘモシアニンという物質が含まれているためです。さらにタコは9つの脳を持っています。",
            "cta": "こんな面白い雑学をもっと知りたい方はフォローをお願いします！",
            "tags": ["#雑学", "#タコ", "#生き物", "#豆知識", "#shorts"],
            "thumbnail_text": "タコの心臓は3つ！",
            "background_keyword": "ocean octopus",
            "full_script": "タコには心臓が何個あるか知っていますか？実はタコには3つの心臓があります。2つのエラ心臓と1つの体心臓です。しかもタコの血液は青色！ヘモシアニンという物質が含まれているためです。さらにタコは9つの脳を持っています。こんな面白い雑学をもっと知りたい方はフォローをお願いします！",
        }
        print(json.dumps(mock_result, ensure_ascii=False, indent=2))
    else:
        result = generate_script()
        print(json.dumps(result, ensure_ascii=False, indent=2))
