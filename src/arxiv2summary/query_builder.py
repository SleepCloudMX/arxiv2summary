from __future__ import annotations

import re
from dataclasses import dataclass

from .config import QueryConfig
from .utils import to_roman


SECTION_PATTERN = re.compile(r"\\section\*?\{([^}]*)\}")


@dataclass
class BuiltQuery:
    target_name: str
    output_file: str | None
    system_prompt: str
    final_prompt: str
    model_prompt: str
    print_prompt: bool
    translate_abstract: bool
    mode: str
    section_name: str | None = None


def extract_sections(tex_text: str) -> list[str]:
    sections = [m.group(1).strip() for m in SECTION_PATTERN.finditer(tex_text)]
    return [s for s in sections if s]


def build_queries(query_configs: list[QueryConfig], tex_text: str) -> list[BuiltQuery]:
    sections = extract_sections(tex_text)
    built: list[BuiltQuery] = []

    for target in query_configs:
        few_shot_block = "\n\n".join(target.few_shot).strip()

        if target.mode == "section" and sections:
            for index, section_name in enumerate(sections, start=1):
                section_token = f"第{index}章 {to_roman(index)} {section_name}"
                prompt = target.prompt_template.replace("$section", section_token)
                model_prompt = prompt
                if few_shot_block:
                    model_prompt = f"示例：\n{few_shot_block}\n\n任务：\n{prompt}"
                built.append(
                    BuiltQuery(
                        target_name=target.name,
                        output_file=target.output_file,
                        system_prompt=target.system_prompt,
                        final_prompt=prompt,
                        model_prompt=model_prompt,
                        print_prompt=target.print_prompt,
                        translate_abstract=target.translate_abstract,
                        mode=target.mode,
                        section_name=section_name,
                    )
                )
        else:
            prompt = target.prompt_template
            model_prompt = prompt
            if few_shot_block:
                model_prompt = f"示例：\n{few_shot_block}\n\n任务：\n{prompt}"
            built.append(
                BuiltQuery(
                    target_name=target.name,
                    output_file=target.output_file,
                    system_prompt=target.system_prompt,
                    final_prompt=prompt,
                    model_prompt=model_prompt,
                    print_prompt=target.print_prompt,
                    translate_abstract=target.translate_abstract,
                    mode=target.mode,
                    section_name=None,
                )
            )

    return built


def extract_section_block(tex_text: str, section_name: str) -> str:
    escaped = re.escape(section_name)
    pattern = re.compile(
        rf"\\section\*?\{{{escaped}\}}(?P<body>.*?)(?=\\section\*?\{{|\\end\{{document\}}|\Z)",
        re.DOTALL,
    )
    match = pattern.search(tex_text)
    if not match:
        return tex_text
    return f"\\section{{{section_name}}}\n{match.group('body')}"
