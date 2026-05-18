from __future__ import annotations

import io
import logging
import re
import tarfile
from pathlib import Path

import requests
from tqdm import tqdm


ARXIV_ID_PATTERN = re.compile(r"(?P<id>\d{4}\.\d{4,5}(v\d+)?)")


def normalize_arxiv_id(arxiv_ref: str) -> str:
    text = arxiv_ref.strip()
    # arXiv URL
    url_match = re.search(r"arxiv\.org/abs/(?P<id>\d{4}\.\d{4,5}(v\d+)?)", text)
    if url_match:
        return url_match.group("id")
    # arXiv: 前缀
    if text.lower().startswith("arxiv:"):
        text = text[len("arxiv:"):].strip()
    # 严格全匹配纯 ID（如 2309.16739 或 2309.16739v4），防止 src/2309.16739v4/ 误判
    if ARXIV_ID_PATTERN.fullmatch(text):
        return text
    raise ValueError(f"无法识别 arXiv 编号: {arxiv_ref}")


def download_and_extract_arxiv_source(arxiv_id: str, source_dir: Path, logger: logging.Logger) -> Path:
    source_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://arxiv.org/e-print/{arxiv_id}"
    logger.info("下载 arXiv 源码: %s", url)
    response = requests.get(url, stream=True, timeout=120)
    response.raise_for_status()

    total = int(response.headers.get("content-length", 0))
    chunks: list[bytes] = []
    with tqdm(total=total, unit="B", unit_scale=True, desc="下载 arXiv 源码") as pbar:
        for chunk in response.iter_content(chunk_size=8192):
            chunks.append(chunk)
            pbar.update(len(chunk))

    tar_stream = io.BytesIO(b"".join(chunks))
    try:
        with tarfile.open(fileobj=tar_stream, mode="r:*") as archive:
            archive.extractall(source_dir)
    except tarfile.ReadError as error:
        raise RuntimeError("arXiv 返回内容不是可解析的源码压缩包") from error
    return source_dir


def _find_main_tex(source_dir: Path) -> Path:
    tex_files = sorted(source_dir.rglob("*.tex"))
    if not tex_files:
        raise FileNotFoundError("源码目录中未找到 .tex 文件")

    scored: list[tuple[int, int, Path]] = []
    for path in tex_files:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        score_doc = 1 if "\\begin{document}" in content else 0
        score_len = len(content)
        scored.append((score_doc, score_len, path))

    if not scored:
        raise FileNotFoundError("无法读取任何 .tex 文件")
    scored.sort(reverse=True)
    return scored[0][2]


def _inline_inputs(main_file: Path, root: Path, visited: set[Path]) -> str:
    resolved = main_file.resolve()
    if resolved in visited:
        return ""
    visited.add(resolved)

    text = main_file.read_text(encoding="utf-8", errors="ignore")
    pattern = re.compile(r"\\(input|include)\{([^}]+)\}")

    def replace(match: re.Match[str]) -> str:
        raw_target = match.group(2).strip()
        target = (main_file.parent / raw_target)
        candidates = [target]
        if target.suffix == "":
            candidates.append(target.with_suffix(".tex"))
        for candidate in candidates:
            resolved = candidate.resolve()
            if candidate.exists() and candidate.is_file() and (resolved == root or root in resolved.parents):
                try:
                    return _inline_inputs(candidate, root, visited)
                except OSError:
                    return match.group(0)
        return match.group(0)

    return pattern.sub(replace, text)


def flatten_tex_from_source(source_dir: Path, output_tex: Path, logger: logging.Logger) -> Path:
    main_tex = _find_main_tex(source_dir)
    logger.info("检测主 tex 文件: %s", main_tex)
    flattened = _inline_inputs(main_tex, source_dir.resolve(), visited=set())
    output_tex.write_text(flattened, encoding="utf-8")
    return output_tex


def _try_flatten_with_python_api(arxiv_id: str, output_tex: Path, logger: logging.Logger) -> bool:
    """优先使用 arxiv-to-prompt 的 Python API，失败则返回 False 交由本地方案处理。"""
    try:
        from arxiv_to_prompt import process_latex_source  # type: ignore[import-untyped]

        logger.info("使用 arxiv-to-prompt Python API 获取并展平 tex: %s", arxiv_id)
        latex_source = process_latex_source(arxiv_id, keep_comments=False)
        output_tex.parent.mkdir(parents=True, exist_ok=True)
        output_tex.write_text(latex_source, encoding="utf-8")
        logger.info("arxiv-to-prompt 处理完成 -> %s", output_tex)
        return True
    except ImportError:
        logger.warning("arxiv-to-prompt 未安装，切换至本地下载+展平方案")
        return False
    except Exception as error:
        logger.warning("arxiv-to-prompt Python API 失败: %s，切换至本地下载+展平方案", error)
        return False


def prepare_flattened_tex(arxiv_ref: str, source_dir: Path, output_tex: Path, logger: logging.Logger) -> tuple[str, Path]:
    arxiv_id = normalize_arxiv_id(arxiv_ref)
    if not _try_flatten_with_python_api(arxiv_id, output_tex, logger):
        download_and_extract_arxiv_source(arxiv_id, source_dir, logger)
        flatten_tex_from_source(source_dir, output_tex, logger)
    return arxiv_id, output_tex


def prepare_local_tex(source_path: Path, output_tex: Path, logger: logging.Logger) -> str:
    """处理本地 tex 源码：展平 \\input/\\include，写入 paper.tex。

    返回用作目录命名的标识符（文件 stem）。
    """
    if source_path.is_dir():
        flatten_tex_from_source(source_path, output_tex, logger)
        return source_path.resolve().name
    elif source_path.suffix.lower() == ".tex":
        source_dir = source_path.resolve().parent
        flattened = _inline_inputs(source_path, source_dir, visited=set())
        output_tex.parent.mkdir(parents=True, exist_ok=True)
        output_tex.write_text(flattened, encoding="utf-8")
        logger.info("本地 tex 展平完成: %s -> %s", source_path, output_tex)
        return source_path.stem
    else:
        raise ValueError(f"不支持的本地源文件类型: {source_path}，请提供 .tex 文件或包含 .tex 文件的目录")


_TITLE_PATTERN = re.compile(
    r"\\title\s*(?:\[.*?\])?\s*\{(?P<title>[^}]+)\}",
    re.DOTALL,
)
_ABSTRACT_PATTERN = re.compile(
    r"\\begin\s*\{abstract\}(?P<body>.*?)\\end\s*\{abstract\}",
    re.DOTALL,
)


def extract_title(tex_text: str) -> str:
    """从展平 LaTeX 中提取 \\title{...}，失败返回空字符串。"""
    match = _TITLE_PATTERN.search(tex_text)
    if not match:
        return ""
    raw = match.group("title")
    # 简单清理 LaTeX 命令（\\footnote{...} 等）
    raw = re.sub(r"\\[a-zA-Z]+\s*\{[^}]*\}", "", raw)
    raw = re.sub(r"\\[a-zA-Z]+", "", raw)
    return " ".join(raw.split())


def extract_abstract(tex_text: str) -> str:
    """从展平 LaTeX 中提取 \\begin{abstract}...\\end{abstract}，失败返回空字符串。"""
    match = _ABSTRACT_PATTERN.search(tex_text)
    if not match:
        return ""
    raw = match.group("body")
    raw = re.sub(r"%.*?$", "", raw, flags=re.MULTILINE)
    return raw.strip()
