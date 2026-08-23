"""Render document index as compact Markdown for agent system prompts."""

from __future__ import annotations

from services.document_context.models import DocumentIndex, DocumentSection


def render_section_md(section: DocumentSection, depth: int = 0) -> list[str]:
    prefix = "#" * min(4, 2 + depth)
    lines = [
        f"{prefix} {section.section_title} (pages {section.page_start}–{section.page_end})",
        section.summary,
    ]
    if section.keywords:
        lines.append(f"*Keywords: {', '.join(section.keywords)}*")
    lines.append("")
    for sub in section.subsections:
        lines.extend(render_section_md(sub, depth + 1))
    return lines


def render_index_markdown(index: DocumentIndex, max_sections: int = 80) -> str:
    lines = [
        "# Document Index",
        "",
        f"**File:** {index.filename}",
        f"**Pages:** {index.page_count}",
        f"**Sections:** {len(index.sections)}",
        "",
        "Use `readSpecPages` or `readSpecSection` to fetch raw text. Cite page numbers in answers.",
        "",
    ]
    for section in index.sections[:max_sections]:
        lines.extend(render_section_md(section))
    if len(index.sections) > max_sections:
        lines.append(f"_({len(index.sections) - max_sections} more sections omitted from prompt index.)_")
    return "\n".join(lines).strip()
