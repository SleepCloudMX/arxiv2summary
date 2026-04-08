from __future__ import annotations

import re
from pathlib import Path


def slugify(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]", " ", value).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"[^\w\-\u4e00-\u9fff]", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip("_") or "paper"


def to_roman(number: int) -> str:
    mapping = [
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ]
    result = []
    remaining = max(1, number)
    for value, symbol in mapping:
        while remaining >= value:
            result.append(symbol)
            remaining -= value
    return "".join(result)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
