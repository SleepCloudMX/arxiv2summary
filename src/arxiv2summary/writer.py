from __future__ import annotations

from pathlib import Path


class OutputWriter:
    def __init__(self, base_dir: Path, mode: str, single_file: str) -> None:
        self.base_dir = base_dir
        self.mode = mode
        self.single_path = self.base_dir / single_file
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._paper_title: str = ""
        self._arxiv_id: str = ""

    def set_paper_info(self, paper_title: str, arxiv_id: str) -> None:
        """设置论文标题和 arXiv ID，供首次写入文件时生成头部。"""
        self._paper_title = paper_title
        self._arxiv_id = arxiv_id

    def _write_file_header(self, handle: object, arxiv_id: str, paper_title: str) -> None:
        """写论文标题 + arXiv 链接 + [TOC]。"""
        title_line = f"### {paper_title}" if paper_title else f"### arXiv:{arxiv_id}"
        handle.write(title_line + "\n\n")  # type: ignore[union-attr]
        if arxiv_id:
            abs_url = f"https://arxiv.org/abs/{arxiv_id}"
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
            html_url = f"https://arxiv.org/html/{arxiv_id}"
            handle.write(f"arXiv: [abs]({abs_url}) / [pdf]({pdf_url}) / [html]({html_url})\n\n")  # type: ignore[union-attr]
        handle.write("[TOC]\n\n")  # type: ignore[union-attr]

    def append(self, target_name: str, output_file: str | None, prompt: str, answer: str, print_prompt: bool = False) -> Path:
        if self.mode == "multi":
            file_name = output_file or f"output-{target_name}.md"
            out_path = self.base_dir / file_name
        else:
            out_path = self.single_path

        is_new = not out_path.exists() or out_path.stat().st_size == 0
        with out_path.open("a", encoding="utf-8") as handle:
            if is_new:
                self._write_file_header(handle, self._arxiv_id, self._paper_title)
            if print_prompt:
                handle.write("### Prompt\n\n")
                handle.write(prompt.strip() + "\n\n")
                handle.write("### Answer\n\n")
            handle.write(answer.strip() + "\n\n---\n\n")
        return out_path
