from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from .config import LLMConfig


@dataclass
class GenerationStats:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_chars: int
    completion_chars: int
    total_chars: int
    token_source: str


@dataclass
class GenerationResult:
    text: str
    stats: GenerationStats


class LLMClient:
    def __init__(self, cfg: LLMConfig) -> None:
        self.cfg = cfg
        self.provider = cfg.provider.lower().strip()
        self.ollama_client: Any | None = None
        self.openai_client: OpenAI | None = None

        if self.provider == "ollama":
            try:
                import ollama  # type: ignore[import-untyped]
            except ImportError as error:
                raise ImportError("provider=ollama 需要安装 ollama Python 包") from error

            host = (cfg.base_url or "http://localhost:11434").rstrip("/")
            if host.endswith("/v1"):
                host = host[:-3]
            self.ollama_client = ollama.Client(host=host)
        else:
            key = os.getenv(cfg.api_key_env)
            if not key:
                raise ValueError(f"未设置 API Key 环境变量: {cfg.api_key_env}")
            self.openai_client = OpenAI(
                base_url=cfg.base_url,
                api_key=key,
                timeout=cfg.timeout_sec,
            )

    def _ollama_options(self) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "temperature": self.cfg.temperature,
            "num_predict": self.cfg.max_tokens,
            "num_ctx": self.cfg.num_ctx,
            "repeat_penalty": self.cfg.repeat_penalty,
        }
        if self.cfg.stop:
            opts["stop"] = self.cfg.stop
        return opts

    def _estimate_tokens(self, text: str) -> int:
        try:
            from arxiv_to_prompt import count_tokens  # type: ignore[import-untyped]

            return int(count_tokens(text))
        except Exception:
            pieces = re.findall(r"[\u4e00-\u9fff]|\w+|[^\w\s]", text, flags=re.UNICODE)
            return len(pieces)

    def _build_stats(
        self,
        messages: list[dict[str, Any]],
        text: str,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        token_source: str = "estimated",
    ) -> GenerationStats:
        prompt_text = "\n".join(str(message.get("content") or "") for message in messages)
        prompt_chars = len(prompt_text)
        completion_chars = len(text)
        total_chars = prompt_chars + completion_chars

        final_prompt_tokens = prompt_tokens if prompt_tokens is not None else self._estimate_tokens(prompt_text)
        final_completion_tokens = completion_tokens if completion_tokens is not None else self._estimate_tokens(text)
        final_total_tokens = final_prompt_tokens + final_completion_tokens

        return GenerationStats(
            prompt_tokens=final_prompt_tokens,
            completion_tokens=final_completion_tokens,
            total_tokens=final_total_tokens,
            prompt_chars=prompt_chars,
            completion_chars=completion_chars,
            total_chars=total_chars,
            token_source=token_source if prompt_tokens is not None and completion_tokens is not None else "estimated",
        )

    def _generate_ollama(self, messages: list[dict[str, Any]]) -> GenerationResult:
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        if self.cfg.stream:
            print("\n【回答开始】", flush=True)
            pieces: list[str] = []
            if self.ollama_client is None:
                raise RuntimeError("ollama client 未初始化")
            stream = self.ollama_client.chat(
                model=self.cfg.model,
                messages=messages,
                stream=True,
                options=self._ollama_options(),
            )
            for chunk in stream:
                message = chunk.get("message", {}) or {}
                thinking = message.get("thinking") or ""
                content = message.get("content") or ""
                prompt_eval_count = chunk.get("prompt_eval_count")
                eval_count = chunk.get("eval_count")
                if isinstance(prompt_eval_count, int):
                    prompt_tokens = prompt_eval_count
                if isinstance(eval_count, int):
                    completion_tokens = eval_count
                if thinking:
                    print(thinking, end="", flush=True)
                if content:
                    print(content, end="", flush=True)
                    pieces.append(content)
            print("\n【回答结束】\n", flush=True)
            text = "".join(pieces).strip()
            return GenerationResult(
                text=text,
                stats=self._build_stats(messages, text, prompt_tokens, completion_tokens, token_source="provider"),
            )

        if self.ollama_client is None:
            raise RuntimeError("ollama client 未初始化")
        response = self.ollama_client.chat(
            model=self.cfg.model,
            messages=messages,
            options=self._ollama_options(),
        )
        message = response.get("message", {}) or {}
        text = (message.get("content") or "").strip()
        prompt_eval_count = response.get("prompt_eval_count")
        eval_count = response.get("eval_count")
        if isinstance(prompt_eval_count, int):
            prompt_tokens = prompt_eval_count
        if isinstance(eval_count, int):
            completion_tokens = eval_count
        return GenerationResult(
            text=text,
            stats=self._build_stats(messages, text, prompt_tokens, completion_tokens, token_source="provider"),
        )

    def _generate_openai_compatible(self, messages: list[dict[str, Any]]) -> GenerationResult:
        if self.cfg.stream:
            print("\n【回答开始】", flush=True)
            chunks: list[str] = []
            prompt_tokens: int | None = None
            completion_tokens: int | None = None
            if self.openai_client is None:
                raise RuntimeError("openai client 未初始化")
            stream = self.openai_client.chat.completions.create(
                model=self.cfg.model,
                temperature=self.cfg.temperature,
                max_tokens=self.cfg.max_tokens,
                messages=messages,
                stream=True,
            )
            for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    prompt_value = getattr(usage, "prompt_tokens", None)
                    completion_value = getattr(usage, "completion_tokens", None)
                    if isinstance(prompt_value, int):
                        prompt_tokens = prompt_value
                    if isinstance(completion_value, int):
                        completion_tokens = completion_value
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    print(delta, end="", flush=True)
                    chunks.append(delta)
            print("\n【回答结束】\n", flush=True)
            text = "".join(chunks).strip()
            return GenerationResult(
                text=text,
                stats=self._build_stats(messages, text, prompt_tokens, completion_tokens, token_source="provider"),
            )

        if self.openai_client is None:
            raise RuntimeError("openai client 未初始化")
        completion = self.openai_client.chat.completions.create(
            model=self.cfg.model,
            temperature=self.cfg.temperature,
            max_tokens=self.cfg.max_tokens,
            messages=messages,
        )
        message = completion.choices[0].message
        text = (message.content or "").strip()
        usage = getattr(completion, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage is not None else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage is not None else None
        return GenerationResult(
            text=text,
            stats=self._build_stats(messages, text, prompt_tokens, completion_tokens, token_source="provider"),
        )

    def generate(self, system_prompt: str, user_prompt: str) -> GenerationResult:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        if self.provider == "ollama":
            return self._generate_ollama(messages)
        return self._generate_openai_compatible(messages)
