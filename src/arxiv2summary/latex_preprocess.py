from __future__ import annotations

import logging
import re
from pathlib import Path

from .config import PreprocessingConfig


def _try_expand_with_python_api(raw_text: str, logger: logging.Logger) -> str | None:
    """优先使用 expand-latex-macros 的 Python API，失败则返回 None 交由内置回退处理。"""
    try:
        import expand_latex_macros as _lib  # type: ignore[import-untyped]

        expanded: str = _lib.expand_latex_macros(raw_text)
        logger.info("使用 expand-latex-macros Python API 完成宏展开")
        return expanded
    except ImportError:
        logger.warning("expand-latex-macros 未安装，使用内置回退实现")
        return None
    except Exception as error:
        logger.warning("expand-latex-macros Python API 失败: %s，使用内置回退实现", error)
        return None


def _build_macros(text: str) -> tuple[dict[str, str], dict[str, str], str]:
    simple_macros: dict[str, str] = {}
    one_arg_macros: dict[str, str] = {}

    newcommand_pattern = re.compile(
        r"\\(?:re)?newcommand\s*\{\\([A-Za-z]+)\}\s*(?:\[(\d+)\])?\s*\{([^{}]*)\}"
    )
    def_pattern = re.compile(r"\\def\\([A-Za-z]+)\s*\{([^{}]*)\}")

    def collect_newcommand(match: re.Match[str]) -> str:
        name = match.group(1)
        arg_num = int(match.group(2) or "0")
        body = match.group(3)
        if arg_num == 0:
            simple_macros[name] = body
        elif arg_num == 1:
            one_arg_macros[name] = body
        return ""

    text = newcommand_pattern.sub(collect_newcommand, text)

    def collect_def(match: re.Match[str]) -> str:
        name = match.group(1)
        body = match.group(2)
        simple_macros[name] = body
        return ""

    text = def_pattern.sub(collect_def, text)
    return simple_macros, one_arg_macros, text


def _expand_macros_fallback(text: str, cfg: PreprocessingConfig) -> str:
    simple_macros, one_arg_macros, text = _build_macros(text)
    for _ in range(cfg.macro_max_iterations):
        previous = text

        for name, body in one_arg_macros.items():
            pattern = re.compile(rf"\\{name}\{{([^{{}}]*)\}}")
            text = pattern.sub(lambda m: body.replace("#1", m.group(1)), text)

        for name, body in simple_macros.items():
            pattern = re.compile(rf"\\{name}(?![A-Za-z])")
            text = pattern.sub(body, text)

        if text == previous:
            break
        if len(text) > cfg.macro_max_output_chars:
            raise RuntimeError("宏展开后文本过大，已触发保护阈值")
    return text


def expand_latex_macros(input_tex: Path, output_tex: Path, cfg: PreprocessingConfig, logger: logging.Logger) -> Path:
    output_tex.parent.mkdir(parents=True, exist_ok=True)
    raw_text = input_tex.read_text(encoding="utf-8", errors="ignore")

    if not cfg.expand_macros:
        output_tex.write_text(raw_text, encoding="utf-8")
        return output_tex

    expanded = _try_expand_with_python_api(raw_text, logger)
    if expanded is not None:
        output_tex.write_text(expanded, encoding="utf-8")
        return output_tex

    log_path = output_tex.parent / "macro-expand.log"
    try:
        expanded = _expand_macros_fallback(raw_text, cfg)
        output_tex.write_text(expanded, encoding="utf-8")
    except Exception as error:
        log_path.write_text(f"macro expansion failed: {error}\n", encoding="utf-8")
        logger.exception("宏展开失败，已降级写入原始文本")
        output_tex.write_text(raw_text, encoding="utf-8")
    return output_tex
