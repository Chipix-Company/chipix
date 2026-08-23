"""
Large-document context manager for spec artifacts.

Flow:
  upload spec -> page extraction -> structural index -> agent readSpecPages/searchSpec
  mental-model build -> targeted page excerpts instead of spec_text[:N]

Archival: raw PDF on disk under outputs/.../artifacts/spec.
Working context: compact index.md in system prompt; pages fetched on demand.
"""

from services.document_context.manager import (
    ensure_spec_document_index,
    get_document_context_status,
    get_index_markdown_for_prompt,
    get_spec_context_dir,
    read_spec_pages,
    read_spec_section,
    search_spec,
    spec_text_for_mental_model,
    render_index_markdown,
)

__all__ = [
    "ensure_spec_document_index",
    "get_document_context_status",
    "get_index_markdown_for_prompt",
    "get_spec_context_dir",
    "read_spec_pages",
    "read_spec_section",
    "search_spec",
    "spec_text_for_mental_model",
    "render_index_markdown",
]
