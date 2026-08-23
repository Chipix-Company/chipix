from __future__ import annotations

from services.mental_model.slang_analyzer import (
    SLANG_GUIDANCE,
    SlangAnalysisError,
    SlangStructuralAnalyzer,
)


def is_available() -> bool:
    try:
        SlangStructuralAnalyzer.resolve_slang_binary()
        return True
    except SlangAnalysisError:
        return False


def version() -> str:
    try:
        return SlangStructuralAnalyzer().version()
    except SlangAnalysisError:
        return ""


__all__ = [
    "SLANG_GUIDANCE",
    "SlangAnalysisError",
    "SlangStructuralAnalyzer",
    "is_available",
    "version",
]
