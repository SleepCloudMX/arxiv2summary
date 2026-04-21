from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from .config import get_default_config_path, load_config, write_default_config
from .logging_utils import setup_logging
from .pipeline import run_pipeline


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="arxiv2summary")
    parser.add_argument("arxiv", nargs="?", help="arXiv 链接或编号，例如 1706.03762")
    parser.add_argument("--config", help="配置文件路径；默认使用工作目录 config.yaml 覆盖项目默认配置")
    parser.add_argument(
        "--out",
        default=".",
        metavar="DIR",
        help="输出根目录（默认：当前目录）; 程序将在其下创建 [arxiv_id] 子目录",
    )
    parser.add_argument(
        "--set-env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="写入 .env 中的键值对，可重复使用，如 --set-env OPENAI_API_KEY=sk-xxx",
    )
    parser.add_argument("--debug", action="store_true", help="启用调试日志")
    return parser


def _update_env_file(env_path: Path, assignments: list[str]) -> None:
    if not assignments:
        return

    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            existing[key.strip()] = value

    for item in assignments:
        if "=" not in item:
            raise ValueError(f"无效的 --set-env 参数: {item}，应为 KEY=VALUE")
        key, value = item.split("=", 1)
        existing[key.strip()] = value

    content = "\n".join(f"{key}={value}" for key, value in existing.items()) + "\n"
    env_path.write_text(content, encoding="utf-8")


def _resolve_config_path(workspace_dir: Path, explicit_config: str | None) -> Path:
    if explicit_config:
        config_path = Path(explicit_config)
        if not config_path.is_absolute():
            config_path = workspace_dir / config_path
        return config_path

    workspace_config = workspace_dir / "config.yaml"
    if workspace_config.exists():
        return workspace_config
    return get_default_config_path()


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    workspace_dir = Path.cwd()
    env_path = workspace_dir / ".env"
    _update_env_file(env_path, args.set_env)
    load_dotenv(dotenv_path=env_path)

    # --out 参数：解析为绝对路径，不存在则新建
    out_dir = Path(args.out).expanduser()
    if not out_dir.is_absolute():
        out_dir = workspace_dir / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    config_path = _resolve_config_path(workspace_dir, args.config)

    if not args.arxiv:
        workspace_config = workspace_dir / "config.yaml"
        if not workspace_config.exists():
            write_default_config(workspace_config)
            print(f"已生成默认配置副本: {workspace_config}")
        config_path = workspace_config

        user_input = input("请输入 arXiv 链接/编号（直接回车退出）：").strip()
        if not user_input:
            print("未提供 arXiv 输入，退出。")
            return 0
        arxiv_ref = user_input
    else:
        arxiv_ref = args.arxiv

    cfg = load_config(config_path)
    debug_mode = bool(args.debug or cfg.runtime.debug_logging)

    logger = setup_logging(debug=debug_mode)
    output_dir = run_pipeline(arxiv_ref, cfg, out_dir, logger, config_path=config_path)
    print(f"处理完成，输出目录: {output_dir}")
    return 0
