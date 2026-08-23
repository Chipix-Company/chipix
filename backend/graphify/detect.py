"""Project detection metadata for graph reports."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from graphify.extract import RTL_SUFFIXES


def detect(root: str | Path) -> dict[str, Any]:
    path = Path(root)
    files = (
        [candidate for candidate in path.rglob("*") if candidate.is_file()]
        if path.is_dir()
        else ([path] if path.is_file() else [])
    )
    rtl_files = [candidate for candidate in files if candidate.suffix.lower() in RTL_SUFFIXES]
    suffixes = Counter(candidate.suffix.lower() or "<none>" for candidate in rtl_files)
    return {
        "root": str(path),
        "file_count": len(rtl_files),
        "extensions": dict(sorted(suffixes.items())),
    }
