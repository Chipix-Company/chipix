"""Large simulator-log filtering, structuring, and clustering."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable

from services.verification.uvm_log_parser import UVMLogAnalysis, UVMLogDiagnostic, parse_uvm_logs


IMPORTANT_TOKENS = (
    "uvm_warning",
    "uvm_error",
    "uvm_fatal",
    "*e,",
    "*f,",
    "*w,",
    "mismatch",
    "timeout",
    "assertion",
    "could not be bound",
    "unknown type",
    "unrecognized declaration",
    "uvm report summary",
)


@dataclass
class ErrorCluster:
    id: str
    key: str
    count: int
    first: dict
    last: dict
    diagnostics: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def filter_log_text(text: str, *, context_lines: int = 5) -> str:
    lines = str(text or "").splitlines()
    keep: set[int] = set()
    for index, line in enumerate(lines):
        lowered = line.lower()
        if any(token in lowered for token in IMPORTANT_TOKENS):
            start = max(0, index - context_lines)
            end = min(len(lines), index + context_lines + 1)
            keep.update(range(start, end))
    if not keep:
        return "\n".join(lines[:200])
    return "\n".join(lines[index] for index in sorted(keep))


def analyze_large_logs(
    logs: Iterable[str | bytes],
    *,
    simulator: str = "auto",
) -> dict:
    decoded = [_decode(log) for log in logs]
    filtered = [filter_log_text(text) for text in decoded]
    analysis = parse_uvm_logs(filtered, simulator=simulator)
    clusters = cluster_diagnostics(analysis.diagnostics)
    return {
        "filtered_log": "\n".join(filtered),
        "analysis": analysis.to_dict(),
        "clusters": [cluster.to_dict() for cluster in clusters],
    }


def cluster_diagnostics(diagnostics: list[UVMLogDiagnostic]) -> list[ErrorCluster]:
    buckets: dict[str, list[UVMLogDiagnostic]] = {}
    for diag in diagnostics:
        key = "|".join(
            [
                diag.phase or "unknown",
                diag.code or "GENERIC",
                diag.symbol or "",
                diag.file or "",
                diag.message[:120],
            ]
        )
        buckets.setdefault(key, []).append(diag)

    clusters: list[ErrorCluster] = []
    for key, items in buckets.items():
        clusters.append(
            ErrorCluster(
                id=f"SIM-CLUSTER-{len(clusters) + 1:03d}",
                key=key,
                count=len(items),
                first=items[0].to_dict(),
                last=items[-1].to_dict(),
                diagnostics=[item.to_dict() for item in items[:12]],
            )
        )
    return clusters


def _decode(log: str | bytes) -> str:
    if isinstance(log, bytes):
        return log.decode("utf-8", errors="replace")
    return str(log or "")
