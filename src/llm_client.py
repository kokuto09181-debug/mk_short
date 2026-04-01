"""
LLMクライアント抽象化モジュール
Anthropic API と Ollama（ローカルLLM）を共通インターフェースで切り替える。

設定方法（settings.yaml の ai セクション）:
  backend: "anthropic"   # Anthropic API を使用（デフォルト）
  backend: "ollama"      # ローカル Ollama を使用

  ollama_model: "kwangsuklee/Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled-GGUF:latest"
  ollama_host: "http://localhost:11434"  # 省略可
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

OLLAMA_DEFAULT_MODEL = "kwangsuklee/Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled-GGUF:latest"


def _load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient:
    """
    Anthropic / Ollama を透過的に切り替えられる薄いラッパー。

    使い方:
        client = LLMClient()          # settings.yaml の backend に従う
        client = LLMClient("ollama")  # 強制切り替え
        resp = client.create(system=..., messages=[...], max_tokens=..., temperature=...)
        print(resp.text)
    """

    def __init__(
        self,
        backend: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        ollama_host: Optional[str] = None,
    ):
        config = _load_config()
        ai_cfg = config.get("ai", {})

        self.backend = backend or ai_cfg.get("backend", "anthropic")
        self.model = model or (
            ai_cfg.get("ollama_model", OLLAMA_DEFAULT_MODEL)
            if self.backend == "ollama"
            else ai_cfg.get("model", "claude-haiku-4-5-20251001")
        )
        self.ollama_host = ollama_host or ai_cfg.get("ollama_host", "http://localhost:11434")
        self._api_key = api_key

        logger.info(f"LLMClient: backend={self.backend}, model={self.model}")

    # ─────────────────────────────────────────
    # パブリックAPI
    # ─────────────────────────────────────────

    def create(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """
        LLM にメッセージを送り、LLMResponse を返す。

        Args:
            messages: [{"role": "user"/"assistant", "content": "..."}]
            system: システムプロンプト（空文字列で省略可）
            max_tokens: 最大出力トークン数
            temperature: 温度（0.0〜1.0）
        """
        if self.backend == "ollama":
            return self._create_ollama(messages, system, max_tokens, temperature)
        else:
            return self._create_anthropic(messages, system, max_tokens, temperature)

    # ─────────────────────────────────────────
    # Anthropic バックエンド
    # ─────────────────────────────────────────

    def _create_anthropic(self, messages, system, max_tokens, temperature) -> LLMResponse:
        import anthropic
        api_key = self._api_key or os.environ.get("ANTHROPIC_API_KEY")
        client = anthropic.Anthropic(api_key=api_key)

        kwargs: dict = dict(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=messages,
        )
        if system:
            kwargs["system"] = system

        resp = client.messages.create(**kwargs)
        text = resp.content[0].text.strip()
        logger.debug(
            f"Anthropic tokens: in={resp.usage.input_tokens} out={resp.usage.output_tokens}"
        )
        return LLMResponse(
            text=text,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )

    # ─────────────────────────────────────────
    # Ollama バックエンド
    # ─────────────────────────────────────────

    def _create_ollama(self, messages, system, max_tokens, temperature) -> LLMResponse:
        import ollama

        # Ollama はシステムプロンプトを messages の先頭に system ロールで渡す
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        client = ollama.Client(host=self.ollama_host)
        resp = client.chat(
            model=self.model,
            messages=full_messages,
            options={
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        )

        text = resp.message.content.strip()
        # Ollama はトークン数を eval_count で返す
        input_tokens = getattr(resp, "prompt_eval_count", 0) or 0
        output_tokens = getattr(resp, "eval_count", 0) or 0
        logger.debug(
            f"Ollama tokens: in={input_tokens} out={output_tokens}"
        )
        return LLMResponse(text=text, input_tokens=input_tokens, output_tokens=output_tokens)


def create_client(
    backend: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> LLMClient:
    """設定に従い LLMClient を生成するファクトリ関数"""
    return LLMClient(backend=backend, model=model, api_key=api_key)
