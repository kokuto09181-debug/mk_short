"""
コンテンツ生成モジュール
Claude API (Haiku) を使ってYouTubeショート動画の脚本を生成する
"""

import json
import logging
import os
import random
from pathlib import Path
from typing import Optional

import anthropic
import yaml
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"


def load_config() -> dict:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_prompts() -> dict:
    with open(CONFIG_DIR / "prompts.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


class ContentGenerator:
    def __init__(self, api_key: Optional[str] = None):
        self.client = anthropic.Anthropic(
            api_key=api_key or os.environ["ANTHROPIC_API_KEY"]
        )
        self.config = load_config()
        self.prompts = load_prompts()
        self.ai_config = self.config["ai"]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    def generate_script(self, topic: str) -> dict:
        """指定テーマの動画脚本をJSONで生成する"""
        prompt_template = self.prompts["script_generation"]
        user_message = prompt_template["user"].format(topic=topic)
        system_message = prompt_template["system"]

        logger.info(f"脚本生成開始: {topic}")

        message = self.client.messages.create(
            model=self.ai_config["model"],
            max_tokens=self.ai_config["max_tokens"],
            temperature=self.ai_config["temperature"],
            system=system_message,
            messages=[{"role": "user", "content": user_message}],
        )

        raw_text = message.content[0].text.strip()

        # JSONブロックの抽出
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0].strip()

        script = json.loads(raw_text)
        script["topic"] = topic
        script["model"] = self.ai_config["model"]

        # 入力トークン使用量ログ（コスト管理）
        logger.info(
            f"トークン使用: input={message.usage.input_tokens}, "
            f"output={message.usage.output_tokens}"
        )

        return script

    def pick_topic(self, niche_name: Optional[str] = None) -> tuple[str, list[str]]:
        """設定からテーマをランダムに選ぶ。(topic, hashtags) を返す"""
        niches = self.config["content"]["niches"]

        if niche_name:
            niche = next((n for n in niches if n["name"] == niche_name), None)
            if niche is None:
                raise ValueError(f"ニッチ '{niche_name}' が見つかりません")
        else:
            niche = random.choice(niches)

        topic = random.choice(niche["topics"])
        hashtags = niche["hashtags"]
        logger.info(f"選択ニッチ: {niche['name']} / テーマ: {topic}")
        return topic, hashtags

    def build_full_narration(self, script: dict) -> str:
        """脚本からTTS読み上げ用の連続テキストを組み立てる"""
        parts = [script["hook"]]
        for section in script.get("sections", []):
            if section.get("heading"):
                parts.append(section["heading"])
            parts.append(section["content"])
        parts.append(script["cta"])
        return "。".join(parts)

    def build_description(self, script: dict, hashtags: list[str]) -> str:
        """YouTube動画説明文を組み立てる"""
        tags_str = " ".join(hashtags)
        return f"{script['description']}\n\n{tags_str}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gen = ContentGenerator()
    topic, hashtags = gen.pick_topic()
    script = gen.generate_script(topic)
    print(json.dumps(script, ensure_ascii=False, indent=2))
    print("\n--- 読み上げテキスト ---")
    print(gen.build_full_narration(script))
