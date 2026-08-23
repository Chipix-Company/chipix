"""Chunk RTL source files for FTS indexing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_MODULE_START = re.compile(
    r"^\s*(module|interface|package|program)\s+(\w+)",
    re.MULTILINE | re.IGNORECASE,
)


@dataclass(frozen=True)
class RtlChunk:
    filepath: str
    module_name: str
    start_line: int
    body: str


def chunk_rtl_text(filepath: str, text: str, *, max_chars: int = 480) -> list[RtlChunk]:
    """Split RTL text into module-scoped chunks suitable for FTS."""
    normalized = str(text or "").replace("\r\n", "\n")
    if not normalized.strip():
        return []

    matches = list(_MODULE_START.finditer(normalized))
    if not matches:
        return _split_plain(filepath, normalized, max_chars=max_chars)

    chunks: list[RtlChunk] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        block = normalized[start:end].strip()
        if not block:
            continue
        module_name = match.group(2)
        start_line = normalized[:start].count("\n") + 1
        for piece in _split_plain(filepath, block, max_chars=max_chars, module_name=module_name, start_line=start_line):
            chunks.append(piece)
    return chunks


def _split_plain(
    filepath: str,
    text: str,
    *,
    max_chars: int,
    module_name: str = "",
    start_line: int = 1,
) -> list[RtlChunk]:
    lines = text.splitlines()
    chunks: list[RtlChunk] = []
    buf: list[str] = []
    line_no = start_line

    def flush() -> None:
        nonlocal buf, line_no
        if not buf:
            return
        body = "\n".join(buf).strip()
        if body:
            chunks.append(
                RtlChunk(
                    filepath=filepath,
                    module_name=module_name,
                    start_line=line_no,
                    body=body[:max_chars],
                )
            )
        buf = []

    for line in lines:
        candidate = "\n".join(buf + [line])
        if buf and len(candidate) > max_chars:
            flush()
            line_no += len(buf) if buf else 0
        buf.append(line)
    flush()
    return chunks


def iter_rtl_files_from_path(artifact_path: Path) -> list[tuple[str, str]]:
    """Return (relative_path, text) pairs from a single RTL file or project archive."""
    from services.rtl_project_package import is_rtl_archive_filename, is_rtl_source_filename

    path = Path(artifact_path)
    if not path.exists():
        return []

    if is_rtl_archive_filename(path.name):
        import zipfile

        pairs: list[tuple[str, str]] = []
        with zipfile.ZipFile(path, "r") as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = info.filename.replace("\\", "/")
                if not is_rtl_source_filename(name):
                    continue
                try:
                    raw = archive.read(info)
                    text = raw.decode("utf-8", errors="ignore")
                except Exception:
                    continue
                if text.strip():
                    pairs.append((name, text))
        return pairs

    if is_rtl_source_filename(path.name):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []
        return [(path.name, text)] if text.strip() else []

    return []
