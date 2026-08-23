"""Allowlist of in-app user guides (Documentation/*.md)."""

from __future__ import annotations

# Keep in sync with frontend/src/components/thread-first/documentationCatalog.js
USER_DOCUMENTATION_FILES: tuple[str, ...] = (
    "getting-started.md",
    "workspace-layout.md",
    "the-thread.md",
    "designing-rtl.md",
    "running-verification.md",
    "understanding-results.md",
    "patches-and-diffs.md",
    "task-board.md",
    "files-and-editor.md",
    "keyboard-shortcuts.md",
    "glossary.md",
    "troubleshooting.md",
)

USER_DOCUMENTATION_ALLOWLIST = frozenset(USER_DOCUMENTATION_FILES)


def is_user_facing_doc(name: str) -> bool:
    return name in USER_DOCUMENTATION_ALLOWLIST


def docs_index_sort_key(name: str) -> tuple[int, int]:
    try:
        return (0, USER_DOCUMENTATION_FILES.index(name))
    except ValueError:
        return (1, 0)
