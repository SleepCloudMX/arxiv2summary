from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path

from .arxiv_source import extract_abstract, extract_title, normalize_arxiv_id, prepare_flattened_tex
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


_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|]')


def _sanitize_dirname(name: str) -> str:
    """移除 Windows/Linux 文件名中的非法字符，限制总长度。"""
    sanitized = _UNSAFE_CHARS.sub("", name)
    sanitized = " ".join(sanitized.split())
    return sanitized[:120]


def _safe_rename(src: Path, dst: Path, logger: logging.Logger) -> Path:
    if not src.exists():
        return src
    if dst.exists():
        logger.warning("目标目录已存在，跳过重命名: %s", dst)
        return src
    try:
        src.rename(dst)
        logger.info("目录重命名: %s -> %s", src.name, dst.name)
        return dst
    except OSError as err:
        logger.warning("目录重命名失败: %s", err)
        return src


def run_pipeline(
    arxiv_ref: str,
    cfg: AppConfig,
    out_dir: Path,
    logger: logging.Logger,
    config_path: Path | None = None,
) -> Path:
    total_started_at = time.perf_counter()
    arxiv_id = normalize_arxiv_id(arxiv_ref)
    output_dir = out_dir / arxiv_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # 在开始时将实际使用的 config 拷贝到工作目录
    if config_path and config_path.exists():
        shutil.copy2(config_path, output_dir / "config.yaml")

    add_file_handler(output_dir / "arxiv2summary.log")
    logger.info("输出目录: %s", output_dir)

    paper_title = ""
    try:
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

        paper_title = extract_title(cleaned_text)
        abstract_text = extract_abstract(cleaned_text)
        if paper_title:
            logger.info("论文标题: %s", paper_title)
        else:
            logger.warning("未能从 LaTeX 提取论文标题")
        if not abstract_text:
            logger.warning("未能从 LaTeX 提取摘要")

        queries = build_queries(cfg.queries, cleaned_text)
        if not queries:
            raise RuntimeError("配置中没有可执行的 queries")

        llm = LLMClient(cfg.llm)
        writer = OutputWriter(output_dir, cfg.output.mode, cfg.output.single_file)
        writer.set_paper_info(paper_title, arxiv_id)

        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_prompt_chars = 0
        total_completion_chars = 0
        total_query_elapsed = 0.0

        # 跟踪已写入摘要翻译的输出文件（避免重复）
        abstract_written: set[str] = set()

        for idx, query in enumerate(queries, start=1):
            if cfg.runtime.section_context_only and query.section_name:
                context = extract_section_block(cleaned_text, query.section_name)
            else:
                context = cleaned_text

            # 确定该 query 写入的输出文件 key
            if cfg.output.mode == "multi":
                file_key = query.output_file or f"output-{query.target_name}.md"
            else:
                file_key = "__single__"

            # 摘要翻译：在该文件第一次写入之前插入（仅写一次）
            if query.translate_abstract and file_key not in abstract_written:
                abstract_written.add(file_key)
                if abstract_text:
                    logger.info("生成摘要翻译 for %s", file_key)
                    abstract_user_prompt = (
                        "请将以下论文摘要翻译为中文，保留数学公式：\n\n" + abstract_text
                    )
                    abstract_result = llm.generate(query.system_prompt, abstract_user_prompt)
                    abstract_answer = f"#### 摘要\n\n{abstract_result.text.strip()}"
                else:
                    logger.warning("摘要为空，写入占位文本")
                    abstract_answer = "#### 摘要\n\n*（未能从论文 LaTeX 源码中提取摘要，请参阅原文。）*"
                writer.append(query.target_name, query.output_file, "", abstract_answer, print_prompt=False)

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

        # 运行成功：重命名目录
        safe_title = _sanitize_dirname(paper_title)
        new_name = f"[{arxiv_id}] {safe_title}" if safe_title else f"[{arxiv_id}]"
        output_dir = _safe_rename(output_dir, out_dir / new_name, logger)
        return output_dir

    except Exception:
        # 运行失败：重命名目录，保留中间产物
        failed_name = f"[Failed][{arxiv_id}]"
        output_dir = _safe_rename(output_dir, out_dir / failed_name, logger)
        raise
