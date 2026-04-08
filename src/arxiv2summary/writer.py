from __future__ import annotations

from pathlib import Path


class OutputWriter:
    def __init__(self, base_dir: Path, mode: str, single_file: str) -> None:
        self.base_dir = base_dir
        self.mode = mode
        self.single_path = self.base_dir / single_file
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def append(self, target_name: str, output_file: str | None, prompt: str, answer: str, print_prompt: bool = False) -> Path:
        if self.mode == "multi":
            file_name = output_file or f"output-{target_name}.md"
            out_path = self.base_dir / file_name
        else:
            out_path = self.single_path

        is_new = not out_path.exists() or out_path.stat().st_size == 0
        with out_path.open("a", encoding="utf-8") as handle:
            if is_new:
                handle.write("[TOC]\n\n")
            if print_prompt:
                handle.write("### Prompt\n\n")
                handle.write(prompt.strip() + "\n\n")
                handle.write("### Answer\n\n")
            handle.write(answer.strip() + "\n\n---\n\n")
        return out_path
