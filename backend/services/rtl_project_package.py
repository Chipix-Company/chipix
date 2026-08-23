from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from common.paths import outputs_dir

RTL_ARCHIVE_SUFFIXES = {".zip", ".tar", ".tgz", ".tar.gz"}
RTL_SOURCE_SUFFIXES = {".v", ".sv", ".svh", ".vh", ".vhd", ".vhdl"}
RTL_EXTRACTED_DIR_SUFFIXES = ("_extracted", "_formal_sources", "_project")
MAX_ARCHIVE_FILES = 5000
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024


class RtlArchiveError(ValueError):
    """Raised when an uploaded RTL project archive cannot be used safely."""


def is_rtl_archive_filename(filename: str | None) -> bool:
    lowered = (filename or "").lower()
    return any(lowered.endswith(suffix) for suffix in RTL_ARCHIVE_SUFFIXES)


def is_rtl_source_filename(filename: str | None) -> bool:
    return Path(filename or "").suffix.lower() in RTL_SOURCE_SUFFIXES


def find_extracted_rtl_root(path: str | Path) -> Path | None:
    """Return the enclosing extraction root for a member path, if any.

    Packaged desktop builds can persist or replay paths to individual files
    inside an extracted RTL project. The compiler/mental-model pipeline should
    analyze the extraction root, not only that one member.
    """

    candidate = Path(path)
    search = [candidate, *candidate.parents]
    for item in search:
        lowered = item.name.lower()
        if any(lowered.endswith(suffix) for suffix in RTL_EXTRACTED_DIR_SUFFIXES):
            return item
    return None


def archive_for_extracted_rtl_root(root: str | Path) -> Path | None:
    """Best-effort sibling archive lookup for an extracted RTL root."""

    extracted_root = Path(root)
    lowered = extracted_root.name.lower()
    stem = ""
    for suffix in RTL_EXTRACTED_DIR_SUFFIXES:
        if lowered.endswith(suffix):
            stem = extracted_root.name[: -len(suffix)]
            break
    if not stem:
        return None

    for archive_suffix in (".zip", ".tar", ".tgz", ".tar.gz"):
        candidate = extracted_root.parent / f"{stem}{archive_suffix}"
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def short_rtl_extract_root(
    archive_path: str | Path,
    *,
    cache_key: str | None = None,
) -> Path:
    """Return a short, deterministic extraction root for RTL archives.

    Extracting beside the uploaded artifact creates very long Windows paths:
    outputs/orgs/<uuid>/projects/<uuid>/artifacts/rtl/v0001_<uuid>_<name>_extracted/...
    Folder uploads can then hit MAX_PATH while writing nested RTL members.
    """

    archive = Path(archive_path)
    seed = cache_key or str(archive)
    digest = hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:16]
    base = outputs_dir() if os.environ.get("CHIPVERIFY_OUTPUTS_DIR") else archive.parent
    return base / "_rtl_extract" / f"{digest}_extracted"


def extract_rtl_archive(archive_path: str | Path, destination_dir: str | Path) -> dict:
    """Safely extract an RTL project archive and return a small manifest.

    Supports ZIP and TAR-style archives. Paths are normalized and checked so archive
    members cannot write outside the destination directory.
    """

    archive = Path(archive_path)
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)

    try:
        if _is_zip_archive(archive):
            manifest = _extract_zip(archive, destination)
        elif _is_tar_archive(archive):
            manifest = _extract_tar(archive, destination)
        else:
            raise RtlArchiveError(
                "RTL project archive must be .zip, .tar, .tgz, or .tar.gz"
            )
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise RtlArchiveError(f"Unable to read RTL project archive: {exc}") from exc

    if manifest["rtl_file_count"] == 0:
        shutil.rmtree(destination, ignore_errors=True)
        raise RtlArchiveError(
            "RTL project archive did not contain any .v, .sv, .svh, .vh, .vhd, or .vhdl files"
        )

    return manifest


def _is_zip_archive(path: Path) -> bool:
    return path.suffix.lower() == ".zip" or zipfile.is_zipfile(path)


def _is_tar_archive(path: Path) -> bool:
    lowered = path.name.lower()
    return (
        lowered.endswith(".tar")
        or lowered.endswith(".tgz")
        or lowered.endswith(".tar.gz")
        or tarfile.is_tarfile(path)
    )


def _safe_member_parts(raw_name: str) -> tuple[str, ...]:
    normalized = raw_name.replace("\\", "/").strip()
    pure = PurePosixPath(normalized)
    if pure.is_absolute():
        raise RtlArchiveError(f"Archive member uses an absolute path: {raw_name}")

    parts = pure.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise RtlArchiveError(f"Archive member has an unsafe path: {raw_name}")
    return tuple(parts)


def _target_path(destination: Path, raw_name: str) -> Path:
    parts = _safe_member_parts(raw_name)
    target = (destination / Path(*parts)).resolve()
    root = destination.resolve()
    if root != target and root not in target.parents:
        raise RtlArchiveError(f"Archive member escapes extraction directory: {raw_name}")
    return target


def _record_file(
    *,
    destination: Path,
    raw_name: str,
    size: int,
    stream: BinaryIO,
    manifest: dict,
) -> None:
    if manifest["file_count"] >= MAX_ARCHIVE_FILES:
        raise RtlArchiveError(f"RTL project archive exceeds {MAX_ARCHIVE_FILES} files")
    if manifest["total_uncompressed_bytes"] + max(size, 0) > MAX_ARCHIVE_BYTES:
        raise RtlArchiveError(
            f"RTL project archive exceeds {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB uncompressed"
        )

    target = _target_path(destination, raw_name)
    target.parent.mkdir(parents=True, exist_ok=True)

    with open(target, "wb") as output:
        shutil.copyfileobj(stream, output)

    relative_path = target.relative_to(destination.resolve()).as_posix()
    manifest["file_count"] += 1
    manifest["total_uncompressed_bytes"] += max(size, target.stat().st_size)

    if is_rtl_source_filename(relative_path):
        manifest["rtl_file_count"] += 1
        if len(manifest["rtl_files"]) < 100:
            manifest["rtl_files"].append(relative_path)


def _empty_manifest(kind: str) -> dict:
    return {
        "kind": kind,
        "file_count": 0,
        "rtl_file_count": 0,
        "rtl_files": [],
        "total_uncompressed_bytes": 0,
    }


def _extract_zip(archive: Path, destination: Path) -> dict:
    manifest = _empty_manifest("zip")
    with zipfile.ZipFile(archive) as zip_file:
        for info in zip_file.infolist():
            if info.is_dir():
                continue
            with zip_file.open(info) as source:
                _record_file(
                    destination=destination,
                    raw_name=info.filename,
                    size=int(info.file_size or 0),
                    stream=source,
                    manifest=manifest,
                )
    return manifest


def _extract_tar(archive: Path, destination: Path) -> dict:
    manifest = _empty_manifest("tar")
    with tarfile.open(archive) as tar_file:
        for member in tar_file.getmembers():
            if not member.isfile():
                continue
            source = tar_file.extractfile(member)
            if source is None:
                continue
            with source:
                _record_file(
                    destination=destination,
                    raw_name=member.name,
                    size=int(member.size or 0),
                    stream=source,
                    manifest=manifest,
                )
    return manifest
