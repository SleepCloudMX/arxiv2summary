from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from .arxiv_source import normalize_arxiv_id, prepare_flattened_tex
from .config import AppConfig
from .latex_preprocess import expand_latex_macros
from .llm_client import LLMClient
from .logging_utils import add_file_handler
from .query_builder import build_queries, extract_section_block
from .writer import OutputWriter


def _safe_rate(value: int, elapsed: float) -> float:
    if elapsed <= 0:
        return 0.0
    return value / elapsed


def run_pipeline(arxiv_ref: str, cfg: AppConfig, workspace_dir: Path, logger: logging.Logger) -> Path:
    total_started_at = time.perf_counter()
    arxiv_id = normalize_arxiv_id(arxiv_ref)
    output_dir = workspace_dir / arxiv_id
    output_dir.mkdir(parents=True, exist_ok=True)

    add_file_handler(output_dir / "arxiv2summary.log")
    logger.info("输出目录: %s", output_dir)

    paper_tex = output_dir / "paper.tex"
    source_dir = output_dir / "source"
    prepare_flattened_tex(arxiv_ref, source_dir, paper_tex, logger)

    paper_x = output_dir / "paper-x.tex"
    expand_latex_macros(paper_tex, paper_x, cfg.preprocessing, logger)
    cleaned_text = paper_x.read_text(encoding="utf-8", errors="ignore")

    if not cfg.runtime.keep_intermediate:
        if paper_tex.exists():
            paper_tex.unlink()
        if paper_x.exists():
            paper_x.unlink()
        if source_dir.exists():
            shutil.rmtree(source_dir, ignore_errors=True)

    queries = build_queries(cfg.queries, cleaned_text)
    if not queries:
        raise RuntimeError("配置中没有可执行的 queries")

    llm = LLMClient(cfg.llm)
    writer = OutputWriter(output_dir, cfg.output.mode, cfg.output.single_file)
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_prompt_chars = 0
    total_completion_chars = 0
    total_query_elapsed = 0.0

    for idx, query in enumerate(queries, start=1):
        if cfg.runtime.section_context_only and query.section_name:
            context = extract_section_block(cleaned_text, query.section_name)
        else:
            context = cleaned_text

        user_prompt = (
            "以下是论文内容（LaTeX 文本）:\n"
            "<paper>\n"
            f"{context}\n"
            "</paper>\n\n"
            "请按任务执行：\n"
            f"{query.model_prompt}"
        )

        if cfg.output.mode != "multi" and query.output_file and query.output_file != cfg.output.single_file:
            logger.warning(
                "query %s 配置了 output_file=%s，但当前 output.mode=single，因此仍会写入 %s",
                query.target_name,
                query.output_file,
                cfg.output.single_file,
            )

        logger.info("开始执行 query %s/%s: %s", idx, len(queries), query.target_name)
        started_at = time.perf_counter()
        result = llm.generate(query.system_prompt, user_prompt)
        out_path = writer.append(query.target_name, query.output_file, query.final_prompt, result.text, query.print_prompt)
        elapsed = time.perf_counter() - started_at
        stats = result.stats
        total_query_elapsed += elapsed
        total_prompt_tokens += stats.prompt_tokens
        total_completion_tokens += stats.completion_tokens
        total_prompt_chars += stats.prompt_chars
        total_completion_chars += stats.completion_chars

        logger.info(
            "完成 query %s/%s: %s，耗时 %.2f 秒，tokens(prompt=%s, completion=%s, total=%s, source=%s, completion_rate=%.2f tok/s)，chars(prompt=%s, completion=%s, total=%s, completion_rate=%.2f char/s)，输出 -> %s",
            idx,
            len(queries),
            query.target_name,
            elapsed,
            stats.prompt_tokens,
            stats.completion_tokens,
            stats.total_tokens,
            stats.token_source,
            _safe_rate(stats.completion_tokens, elapsed),
            stats.prompt_chars,
            stats.completion_chars,
            stats.total_chars,
            _safe_rate(stats.completion_chars, elapsed),
            out_path,
        )

    total_elapsed = time.perf_counter() - total_started_at
    total_tokens = total_prompt_tokens + total_completion_tokens
    total_chars = total_prompt_chars + total_completion_chars
    logger.info(
        "全部 query 完成：总耗时 %.2f 秒（query 执行耗时 %.2f 秒），tokens(prompt=%s, completion=%s, total=%s, completion_rate=%.2f tok/s)，chars(prompt=%s, completion=%s, total=%s, completion_rate=%.2f char/s)",
        total_elapsed,
        total_query_elapsed,
        total_prompt_tokens,
        total_completion_tokens,
        total_tokens,
        _safe_rate(total_completion_tokens, total_query_elapsed),
        total_prompt_chars,
        total_completion_chars,
        total_chars,
        _safe_rate(total_completion_chars, total_query_elapsed),
    )

    return output_dir
