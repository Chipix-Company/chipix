"""Isolated simulator run directory creation and safety checks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SV_EXTENSIONS = {".sv", ".svh", ".v", ".vh", ".f", ".flist"}


@dataclass
class SandboxManifest:
    run_id: str
    created_at: str
    run_dir: str
    generated_files: list[str] = field(default_factory=list)
    rtl_files: list[str] = field(default_factory=list)
    rtl_checksums: dict[str, str] = field(default_factory=dict)
    copied_rtl: bool = False
    filelist: str = ""
    source_filelist: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def create_run_sandbox(
    *,
    base_dir: str | Path,
    generated_files: Iterable[str | Path],
    rtl_root: str | Path | None = None,
    rtl_files: Iterable[str | Path] | None = None,
    source_filelist: str | Path | None = None,
    run_id: str | None = None,
) -> SandboxManifest:
    run_id = run_id or f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    run_dir = Path(base_dir) / run_id
    generated_dir = run_dir / "generated"
    rtl_dir = run_dir / "rtl_links"
    results_dir = run_dir / "results"
    xcelium_dir = run_dir / "xcelium.d"
    for path in (generated_dir, rtl_dir, results_dir, xcelium_dir):
        path.mkdir(parents=True, exist_ok=True)

    manifest = SandboxManifest(
        run_id=run_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        run_dir=str(run_dir),
        source_filelist=str(source_filelist or ""),
    )

    generated_map: dict[str, str] = {}
    for source in generated_files:
        source_path = Path(source)
        if not source_path.exists() or not source_path.is_file():
            continue
        target = generated_dir / source_path.name
        shutil.copy2(source_path, target)
        manifest.generated_files.append(str(target))
        generated_map[str(source_path.resolve())] = str(target)

    if rtl_files:
        _link_or_copy_rtl_files([Path(path) for path in rtl_files], rtl_dir, manifest)
    elif rtl_root:
        _link_or_copy_rtl(Path(rtl_root), rtl_dir, manifest)

    filelist = generated_dir / "xcelium_filelist.f"
    if source_filelist and Path(source_filelist).exists():
        _rewrite_filelist(filelist, Path(source_filelist), manifest, generated_map)
    else:
        _write_filelist(filelist, manifest)
    manifest.filelist = str(filelist)

    manifest_path = run_dir / "sandbox.json"
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    return manifest


def verify_rtl_unchanged(manifest: SandboxManifest) -> dict:
    changed = []
    missing = []
    for file_path, expected in manifest.rtl_checksums.items():
        path = Path(file_path)
        if not path.exists():
            missing.append(file_path)
            continue
        current = sha256_file(path)
        if current != expected:
            changed.append(file_path)
    return {
        "passed": not changed and not missing,
        "changed": changed,
        "missing": missing,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _link_or_copy_rtl(rtl_root: Path, rtl_dir: Path, manifest: SandboxManifest) -> None:
    if rtl_root.is_file():
        rtl_files = [rtl_root]
        root = rtl_root.parent
    else:
        root = rtl_root
        rtl_files = [
            path
            for path in rtl_root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".sv", ".svh", ".v", ".vh"}
        ]

    for source in rtl_files:
        relative = source.relative_to(root)
        target = rtl_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        manifest.rtl_checksums[str(source)] = sha256_file(source)
        manifest.rtl_files.append(str(target))
        try:
            os.symlink(source, target)
        except Exception:
            shutil.copy2(source, target)
            manifest.copied_rtl = True


def _link_or_copy_rtl_files(rtl_files: list[Path], rtl_dir: Path, manifest: SandboxManifest) -> None:
    existing = [path.resolve() for path in rtl_files if path.exists() and path.is_file()]
    if not existing:
        return
    try:
        common = Path(os.path.commonpath([str(path.parent) for path in existing]))
    except (ValueError, OSError):
        common = existing[0].parent
    for source in existing:
        try:
            relative = source.relative_to(common)
        except ValueError:
            relative = Path(source.name)
        target = rtl_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        manifest.rtl_checksums[str(source)] = sha256_file(source)
        manifest.rtl_files.append(str(target))
        try:
            os.symlink(source, target)
        except Exception:
            shutil.copy2(source, target)
            manifest.copied_rtl = True


def _rewrite_filelist(
    destination: Path,
    source_filelist: Path,
    manifest: SandboxManifest,
    generated_map: dict[str, str],
) -> None:
    """Preserve generator compile ordering while redirecting paths into the sandbox."""
    rtl_map = {
        str(Path(source).resolve()): target
        for source, target in zip(manifest.rtl_checksums.keys(), manifest.rtl_files)
    }
    generated_by_name = {Path(path).name: path for path in manifest.generated_files}
    rtl_by_name = {Path(path).name: path for path in manifest.rtl_files}
    include_dirs = [str(destination.parent)]
    include_dirs.extend(str(Path(path).parent) for path in manifest.rtl_files)
    output = [f"+incdir+{path}" for path in dict.fromkeys(include_dirs)]
    for raw in source_filelist.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("+incdir+"):
            continue
        if line.startswith("//") or line.startswith("#") or line.startswith("-"):
            output.append(raw)
            continue
        source = Path(line.strip('"'))
        if not source.is_absolute():
            source = source_filelist.parent / source
        resolved = str(source.resolve())
        mapped = generated_map.get(resolved) or rtl_map.get(resolved)
        if not mapped:
            mapped = generated_by_name.get(source.name) or rtl_by_name.get(source.name)
        output.append(mapped or raw)
    destination.write_text("\n".join(output) + "\n", encoding="utf-8")


def _write_filelist(filelist: Path, manifest: SandboxManifest) -> None:
    entries: list[str] = []
    generated = [Path(path) for path in manifest.generated_files]
    rtl = [Path(path) for path in manifest.rtl_files]
    entries.extend(str(path) for path in rtl if path.suffix.lower() in {".sv", ".svh", ".v", ".vh"})

    pkg_files = [path for path in generated if path.name.lower().endswith("_pkg.sv")]
    tb_files = [path for path in generated if path.name.lower() == "top_tb.sv"]
    other_sv = [
        path
        for path in generated
        if path.suffix.lower() in {".sv", ".svh", ".v", ".vh"}
        and path not in pkg_files
        and path not in tb_files
    ]
    entries.extend(str(path) for path in other_sv)
    entries.extend(str(path) for path in pkg_files)
    entries.extend(str(path) for path in tb_files)
    filelist.write_text("\n".join(entries) + "\n", encoding="utf-8")
