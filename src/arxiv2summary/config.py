from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "default_config.yaml"


@dataclass
class LLMConfig:
    provider: str = "ollama"
    model: str = "qwen3.5:latest"
    base_url: str = "http://localhost:11434/v1"
    api_key_env: str = "OPENAI_API_KEY"
    timeout_sec: int = 180
    temperature: float = 0.2
    max_tokens: int = 4096
    num_ctx: int = 16384
    repeat_penalty: float = 1.2
    stop: list[str] = field(default_factory=list)
    stream: bool = False


@dataclass
class PreprocessingConfig:
    expand_macros: bool = True
    macro_timeout_sec: int = 60
    macro_max_iterations: int = 8
    macro_max_output_chars: int = 2_000_000


@dataclass
class RuntimeConfig:
    section_context_only: bool = True
    debug_logging: bool = False
    keep_intermediate: bool = True


@dataclass
class OutputConfig:
    mode: str = "single"
    single_file: str = "output.md"


@dataclass
class QueryConfig:
    name: str
    mode: str = "section"
    prompt_template: str = "以笔记形式多层分点总结$section"
    output_file: str | None = None
    system_prompt: str = "你是一个严谨的学术助手，回答请使用 Markdown。"
    few_shot: list[str] = field(default_factory=list)
    print_prompt: bool = False
    translate_abstract: bool = False


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    preprocessing: PreprocessingConfig = field(default_factory=PreprocessingConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    queries: list[QueryConfig] = field(default_factory=list)


def _deep_merge(base: dict[str, Any], custom: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in custom.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def get_default_config_path() -> Path:
    return DEFAULT_CONFIG_PATH


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def default_config_dict() -> dict[str, Any]:
    if not DEFAULT_CONFIG_PATH.exists():
        raise FileNotFoundError(f"默认配置文件不存在: {DEFAULT_CONFIG_PATH}")
    return _read_yaml(DEFAULT_CONFIG_PATH)


def write_default_config(config_path: Path) -> Path:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DEFAULT_CONFIG_PATH, config_path)
    return config_path


def load_config(config_path: Path) -> AppConfig:
    base = default_config_dict()
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as handle:
            custom = yaml.safe_load(handle) or {}
        data = _deep_merge(base, custom)
    else:
        data = base

    llm = LLMConfig(**data.get("llm", {}))
    preprocessing = PreprocessingConfig(**data.get("preprocessing", {}))
    runtime = RuntimeConfig(**data.get("runtime", {}))
    output = OutputConfig(**data.get("output", {}))
    queries = [QueryConfig(**item) for item in data.get("queries", [])]
    return AppConfig(
        llm=llm,
        preprocessing=preprocessing,
        runtime=runtime,
        output=output,
        queries=queries,
    )
