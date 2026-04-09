"""
コンテンツ生成モジュール
Claude API (Haiku) を使って偉人ショート動画の脚本を生成する
日本語・英語の両言語に対応
"""

import json
import logging
import os
import random
from pathlib import Path
from typing import Optional

import yaml
from tenacity import retry, stop_after_attempt, wait_exponential

from llm_client import LLMClient

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"


def load_config() -> dict:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_prompts() -> dict:
    with open(CONFIG_DIR / "prompts.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


class ContentGenerator:
    def __init__(self, api_key: Optional[str] = None, backend: Optional[str] = None):
        self.config = load_config()
        self.prompts = load_prompts()
        self.ai_config = self.config["ai"]
        self.client = LLMClient(backend=backend, api_key=api_key)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    def generate_script(self, figure: dict, language: str = "ja", prompt_key: str = "") -> dict:
        """
        偉人データから動画脚本を生成する。
        language: "ja" or "en"
        prompt_key: カスタムプロンプトキー（例: "script_ja_v2"）。省略時は "script_{language}"
        """
        prompt_key = prompt_key or f"script_{language}"
        prompt_template = self.prompts[prompt_key]

        user_message = prompt_template["user"].format(
            name_ja=figure.get("name_ja", ""),
            name_en=figure.get("name_en", ""),
            birth_year=figure.get("birth_year") or "不明",
            death_year=figure.get("death_year") or "不明",
            field=figure.get("field", ""),
            era=figure.get("era", ""),
            notes=figure.get("notes", ""),
        )

        logger.info(f"脚本生成 [{language}]: {figure.get('name_ja')} / {figure.get('name_en')}")

        resp = self.client.create(
            system=prompt_template["system"],
            messages=[{"role": "user", "content": user_message}],
            max_tokens=self.ai_config["max_tokens"],
            temperature=self.ai_config["temperature"],
        )

        raw_text = resp.text

        # JSONブロックの抽出
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0].strip()

        script = json.loads(raw_text)
        script["language"] = language
        script["figure_name_ja"] = figure.get("name_ja", "")
        script["figure_name_en"] = figure.get("name_en", "")
        script["figure_era"] = figure.get("era", "")
        script["figure_field"] = figure.get("field", "")

        logger.info(f"トークン使用: input={resp.input_tokens}, output={resp.output_tokens}")
        return script

    def generate_both_languages(self, figure: dict) -> tuple[dict, dict]:
        """日本語・英語の脚本を両方生成して返す。(script_ja, script_en)"""
        script_ja = self.generate_script(figure, language="ja")
        script_en = self.generate_script(figure, language="en")
        return script_ja, script_en

    @staticmethod
    def _extract_hook_from_longform(long_script_ja: str) -> tuple[str, str]:
        """長編脚本のHeaderとHookセクションを抽出して返す。(title, hook_text)"""
        title = ""
        for line in long_script_ja.splitlines():
            if line.startswith("タイトル:"):
                title = line.replace("タイトル:", "").strip()
                break

        # セクションをセパレータで分割
        sep = "=============================="
        parts = long_script_ja.split(sep)
        for part in parts:
            stripped = part.strip()
            if stripped.startswith("【Hook】") or stripped.startswith("【Hook】"):
                # 見出し行を除いて本文を取得
                lines = stripped.splitlines()
                body_lines = [l for l in lines[1:] if l.strip()]
                return title, "\n".join(body_lines)
        return title, ""

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    def generate_short_from_longform_hook(self, figure: dict, long_script_ja: str) -> dict:
        """
        長編脚本のHookセクションをベースに日本語ショート脚本を生成する。
        longform_video_id が設定済みの場合に使用し、
        「続きを見たくなる」予告型ショートを作る。
        """
        longform_title, hook_text = self._extract_hook_from_longform(long_script_ja)
        if not hook_text:
            logger.warning("長編HookセクションをパースできなかったためFallback: 通常ショート脚本生成")
            return self.generate_script(figure, language="ja")

        prompt_template = self.prompts["script_ja_from_hook"]
        user_message = prompt_template["user"].format(
            name_ja=figure.get("name_ja", ""),
            longform_title=longform_title,
            hook_text=hook_text,
        )

        logger.info(f"Hook派生ショート脚本生成 [ja]: {figure.get('name_ja')} (longform_title={longform_title!r})")

        resp = self.client.create(
            system=prompt_template["system"],
            messages=[{"role": "user", "content": user_message}],
            max_tokens=self.ai_config["max_tokens"],
            temperature=self.ai_config["temperature"],
        )

        raw_text = resp.text
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0].strip()

        script = json.loads(raw_text)
        script["language"] = "ja"
        script["figure_name_ja"] = figure.get("name_ja", "")
        script["figure_name_en"] = figure.get("name_en", "")
        script["figure_era"] = figure.get("era", "")
        script["figure_field"] = figure.get("field", "")
        script["derived_from_hook"] = True

        logger.info(f"トークン使用: input={resp.input_tokens}, output={resp.output_tokens}")
        return script

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    def generate_new_figures(
        self,
        field: str,
        era_range: str,
        existing_names: list[str],
        count: int = 10,
    ) -> list[dict]:
        """Claudeに新しい偉人候補を提案させる（Notionのストックが減ったとき用）"""
        prompt_template = self.prompts["figure_generation"]
        existing_str = "、".join(existing_names[:50]) if existing_names else "なし"

        user_message = prompt_template["user"].format(
            count=count,
            field=field,
            era_range=era_range,
            existing_names=existing_str,
        )

        logger.info(f"偉人候補生成: field={field}, era={era_range}, count={count}")

        resp = self.client.create(
            system=prompt_template["system"],
            messages=[{"role": "user", "content": user_message}],
            max_tokens=3000,
            temperature=0.9,
        )

        raw_text = resp.text
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0].strip()

        figures = json.loads(raw_text)
        logger.info(f"新規偉人候補: {len(figures)} 件")
        return figures

    def generate_image_prompt(
        self,
        figure: dict,
        section_text: str,
        style: str = "ukiyo-e",
    ) -> str:
        """
        偉人の情報とシーン内容からComfyUI用の英語画像プロンプトを生成する。
        偉人ごとに異なるビジュアル要素（職業・時代・場面）を含む。
        """
        name_ja = figure.get("name_ja", "")
        name_en = figure.get("name_en", "")
        era = figure.get("era", "")
        field = figure.get("field", "")
        notes = figure.get("notes", "")

        system_prompt = (
            "You are an art director for ukiyo-e style illustrations. "
            "Generate a concise English image prompt (max 80 words) for a woodblock print. "
            "Focus on visual elements: scene, composition, lighting, colors, emotions. "
            "Include the person's occupation and distinctive visual traits. "
            "Do NOT include any text, captions, or watermarks in the prompt. "
            "Output only the prompt, no explanations."
        )
        user_prompt = (
            f"Person: {name_ja} ({name_en})\n"
            f"Era: {era} period Japan\n"
            f"Occupation/Field: {field}\n"
            f"Known for: {notes}\n"
            f"Scene to illustrate: {section_text}\n\n"
            f"Generate an ukiyo-e woodblock print prompt for this specific scene."
        )

        try:
            resp = self.client.chat(
                messages=[{"role": "user", "content": user_prompt}],
                system=system_prompt,
                temperature=0.8,
                max_tokens=150,
            )
            return resp.strip()
        except Exception:
            # フォールバック: シンプルなテンプレート
            return (
                f"ukiyo-e style woodblock print, {name_en}, {era} period Japan, "
                f"{field}, {section_text[:60]}, bold outlines, flat colors, masterpiece"
            )

    def build_narration(self, script: dict) -> str:
        """脚本からTTS読み上げ用の連続テキストを組み立てる"""
        parts = [script.get("hook", "")]
        for section in script.get("sections", []):
            if section.get("heading"):
                parts.append(section["heading"])
            parts.append(section.get("content", ""))
        parts.append(script.get("cta", ""))
        sep = "。" if script.get("language") == "ja" else " "
        return sep.join(p for p in parts if p)

    def build_description(self, script: dict, extra_tags: list[str] = None, longform_video_id: str = "") -> str:
        """YouTube動画説明文を組み立てる（出典・参考リンク含む）"""
        desc = script.get("description", "")
        lang = script.get("language", "ja")
        name_ja = script.get("figure_name_ja", "")
        name_en = script.get("figure_name_en", "")

        if lang == "ja":
            base_tags = ["#偉人", "#日本史", "#歴史", "#shorts", "#雑学"]
            sources_header = "【参考・出典】"
            sources_lines = []
            if name_ja:
                url = f"https://ja.wikipedia.org/wiki/{name_ja}"
                sources_lines.append(f"・Wikipedia「{name_ja}」\n  {url}")
            if name_en:
                url_en = "https://en.wikipedia.org/wiki/" + name_en.replace(" ", "_")
                sources_lines.append(f"・Wikipedia \"{name_en}\"\n  {url_en}")
        else:
            base_tags = ["#JapaneseHistory", "#HiddenHeroes", "#Japan", "#Shorts", "#History"]
            sources_header = "【Sources】"
            sources_lines = []
            if name_en:
                url_en = "https://en.wikipedia.org/wiki/" + name_en.replace(" ", "_")
                sources_lines.append(f"・Wikipedia \"{name_en}\"\n  {url_en}")
            if name_ja:
                url = f"https://ja.wikipedia.org/wiki/{name_ja}"
                sources_lines.append(f"・Wikipedia「{name_ja}」（日本語）\n  {url}")

        sources_block = ""
        if sources_lines:
            sources_block = f"\n\n{sources_header}\n" + "\n".join(sources_lines)

        longform_block = ""
        if longform_video_id and lang == "ja":
            longform_block = f"\n\n▶ 続きの長編動画はこちら\nhttps://youtu.be/{longform_video_id}"

        all_tags = base_tags + (extra_tags or [])
        return f"{desc}{longform_block}{sources_block}\n\n{' '.join(all_tags)}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gen = ContentGenerator()
    sample_figure = {
        "name_ja": "平賀源内",
        "name_en": "Hiraga Gennai",
        "birth_year": 1728,
        "death_year": 1780,
        "era": "江戸",
        "field": "科学者・発明家",
        "notes": "エレキテルを復元した江戸の万能天才。殺人事件で獄死という謎の最期。",
    }
    script_ja = gen.generate_script(sample_figure, "ja")
    print("=== 日本語脚本 ===")
    print(json.dumps(script_ja, ensure_ascii=False, indent=2))
    print("\n--- 読み上げテキスト ---")
    print(gen.build_narration(script_ja))
