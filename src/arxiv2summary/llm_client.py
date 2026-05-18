from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import requests
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

        if self.provider == "ollama":
            host = (cfg.base_url or "http://localhost:11434").rstrip("/")
            if host.endswith("/v1"):
                host = host[:-3]
            self._ollama_host = host
        else:
            key = os.getenv(cfg.api_key_env)
            if not key:
                raise ValueError(f"未设置 API Key 环境变量: {cfg.api_key_env}")
            self.openai_client = OpenAI(
                base_url=cfg.base_url,
                api_key=key,
                timeout=cfg.timeout_sec,
            )

    def _estimate_tokens(self, text: str) -> int:
        try:
            from arxiv_to_prompt import count_tokens  # type: ignore[import-untyped]

            return int(count_tokens(text))
        except Exception:
            pieces = re.findall(r"[一-鿿]|\w+|[^\w\s]", text, flags=re.UNICODE)
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
        url = f"{self._ollama_host}/api/chat"
        body: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "stream": self.cfg.stream,
            "think": self.cfg.think,
            "options": {
                "temperature": self.cfg.temperature,
                "num_predict": self.cfg.max_tokens,
                "num_ctx": self.cfg.num_ctx,
                "repeat_penalty": self.cfg.repeat_penalty,
            },
        }
        if self.cfg.stop:
            body["options"]["stop"] = self.cfg.stop

        prompt_tokens: int | None = None
        completion_tokens: int | None = None

        if self.cfg.stream:
            print("\n【回答开始】", flush=True)
            pieces: list[str] = []
            resp = requests.post(url, json=body, stream=True, timeout=self.cfg.timeout_sec)
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                data = json.loads(line.decode("utf-8"))
                message = data.get("message") or {}
                thinking = message.get("thinking") or ""
                content = message.get("content") or ""
                if data.get("done"):
                    pe = data.get("prompt_eval_count")
                    ec = data.get("eval_count")
                    if isinstance(pe, int):
                        prompt_tokens = pe
                    if isinstance(ec, int):
                        completion_tokens = ec
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

        resp = requests.post(url, json=body, timeout=self.cfg.timeout_sec)
        resp.raise_for_status()
        data = resp.json()
        message = data.get("message") or {}
        text = (message.get("content") or "").strip()
        pe = data.get("prompt_eval_count")
        ec = data.get("eval_count")
        if isinstance(pe, int):
            prompt_tokens = pe
        if isinstance(ec, int):
            completion_tokens = ec
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
                extra_body={"think": self.cfg.think},
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
            extra_body={"think": self.cfg.think},
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
