"""
Patch Service — Create, approve, reject, and apply fix proposals.

Implements the ChipStack-style human-in-the-loop approval flow:
  1. Agent proposes a fix (creates PatchProposal with diff)
  2. Human reviews the diff
  3. Human approves or rejects
  4. If approved, patch is applied automatically
  5. Simulation reruns to verify the fix
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PatchDiff:
    """A diff between original and proposed content."""
    file_path: str
    original_content: str
    proposed_content: str
    unified_diff: str = ""
    lines_added: int = 0
    lines_removed: int = 0

    def compute_diff(self) -> str:
        """Compute unified diff string."""
        orig_lines = self.original_content.splitlines(keepends=True)
        prop_lines = self.proposed_content.splitlines(keepends=True)
        diff = difflib.unified_diff(
            orig_lines, prop_lines,
            fromfile=f"a/{self.file_path}",
            tofile=f"b/{self.file_path}",
        )
        self.unified_diff = "".join(diff)
        self.lines_added = sum(1 for l in self.unified_diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
        self.lines_removed = sum(1 for l in self.unified_diff.splitlines() if l.startswith("-") and not l.startswith("---"))
        return self.unified_diff


@dataclass
class PatchProposalData:
    """Data for creating a patch proposal."""
    id: str = ""
    project_id: str = ""
    source_agent: str = ""     # "unitsim", "formal", "debug"
    title: str = ""
    reason: str = ""           # Why this fix is needed
    diffs: List[PatchDiff] = field(default_factory=list)
    status: str = "awaiting_approval"
    created_at: str = ""

    @property
    def summary(self) -> str:
        total_added = sum(d.lines_added for d in self.diffs)
        total_removed = sum(d.lines_removed for d in self.diffs)
        return (
            f"Patch '{self.title}': {len(self.diffs)} files, "
            f"+{total_added}/-{total_removed} lines"
        )


def create_patch_proposal(
    project_id: str,
    source_agent: str,
    title: str,
    reason: str,
    file_path: str,
    original_content: str,
    proposed_content: str,
    db_session: Any = None,
) -> PatchProposalData:
    """Create a new patch proposal with diff preview."""
    diff = PatchDiff(
        file_path=file_path,
        original_content=original_content,
        proposed_content=proposed_content,
    )
    diff.compute_diff()

    proposal = PatchProposalData(
        id=str(uuid.uuid4()),
        project_id=project_id,
        source_agent=source_agent,
        title=title,
        reason=reason,
        diffs=[diff],
        status="awaiting_approval",
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    # Persist to DB if session available
    if db_session:
        _persist_proposal(proposal, db_session)

    logger.info(f"Created patch proposal: {proposal.summary}")
    return proposal


def approve_patch(
    patch_id: str,
    db_session: Any = None,
) -> Dict[str, Any]:
    """Approve and apply a patch proposal."""
    if db_session:
        db_result = _approve_persisted_patch(patch_id, db_session)
        if db_result is not None:
            return db_result

    proposal = _load_proposal(patch_id, db_session)
    if not proposal:
        return {"status": "error", "message": f"Patch {patch_id} not found"}

    if proposal.status != "awaiting_approval":
        return {"status": "error", "message": f"Patch is {proposal.status}, not awaiting_approval"}

    # Apply each diff
    applied_files = []
    for diff in proposal.diffs:
        try:
            path = Path(diff.file_path)
            if path.exists():
                path.write_text(diff.proposed_content, encoding="utf-8")
                applied_files.append(diff.file_path)
                logger.info(f"Applied patch to: {diff.file_path}")
            else:
                return {"status": "error", "message": f"File not found: {diff.file_path}"}
        except OSError as e:
            return {"status": "error", "message": f"Failed to write {diff.file_path}: {e}"}

    # Update status
    proposal.status = "approved"
    if db_session:
        _update_proposal_status(patch_id, "approved", db_session)

    return {
        "status": "approved",
        "patch_id": patch_id,
        "applied_files": applied_files,
        "message": f"Patch applied to {len(applied_files)} file(s)",
    }


def _approve_persisted_patch(patch_id: str, db_session: Any) -> Optional[Dict[str, Any]]:
    """Apply patch proposals persisted in the main PatchProposal table.

    New UVM log-repair proposals store full proposed file contents in
    metadata_json so approval can safely update generated artifacts after a
    checksum check.
    """
    try:
        from database.models import PatchProposal as PatchProposalModel, ProjectArtifact
    except Exception:
        return None

    row = db_session.query(PatchProposalModel).filter(
        PatchProposalModel.id == patch_id
    ).first()
    if not row:
        return None
    if row.status != "awaiting_approval":
        return {"status": "error", "message": f"Patch is {row.status}, not awaiting_approval"}

    metadata = _json_dict(row.metadata_json)
    files = metadata.get("files") if isinstance(metadata, dict) else None
    if not isinstance(files, list) or not files:
        return None

    applied_files: list[str] = []
    stale_files: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        proposed_content = item.get("proposed_content")
        if not isinstance(proposed_content, str) or not proposed_content:
            return {"status": "error", "message": "Patch metadata does not contain proposed file content"}

        artifact = None
        artifact_id = str(item.get("artifact_id") or "").strip()
        if artifact_id:
            artifact = db_session.query(ProjectArtifact).filter(ProjectArtifact.id == artifact_id).first()

        file_path = Path((artifact.file_path if artifact else item.get("file_path")) or "")
        if not file_path.exists() or not file_path.is_file():
            return {"status": "error", "message": f"File not found: {file_path}"}

        original_checksum = str(item.get("original_checksum") or "").strip()
        current_bytes = file_path.read_bytes()
        current_checksum = hashlib.sha256(current_bytes).hexdigest()
        if original_checksum and current_checksum != original_checksum:
            stale_files.append(str(file_path))
            continue

        new_bytes = proposed_content.encode("utf-8")
        file_path.write_bytes(new_bytes)
        applied_files.append(str(file_path))

        if artifact is not None:
            artifact.checksum_sha256 = hashlib.sha256(new_bytes).hexdigest()
            artifact.size_bytes = len(new_bytes)
            db_session.add(artifact)

    if stale_files:
        row.status = "stale"
        metadata["stale_files"] = stale_files
        row.metadata_json = json.dumps(metadata, default=str)
        db_session.add(row)
        db_session.commit()
        return {
            "status": "error",
            "message": "Patch is stale because one or more files changed after proposal creation.",
            "stale_files": stale_files,
        }

    metadata["decision"] = {
        "status": "approved",
        "applied_files": applied_files,
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    row.status = "approved"
    row.metadata_json = json.dumps(metadata, default=str)
    db_session.add(row)
    db_session.commit()
    return {
        "status": "approved",
        "patch_id": patch_id,
        "applied_files": applied_files,
        "message": f"Patch applied to {len(applied_files)} file(s)",
    }


def reject_patch(
    patch_id: str,
    reason: str = "",
    db_session: Any = None,
) -> Dict[str, Any]:
    """Reject a patch proposal."""
    if db_session:
        _update_proposal_status(patch_id, "rejected", db_session)

    return {
        "status": "rejected",
        "patch_id": patch_id,
        "reason": reason,
    }


def list_pending_patches(
    project_id: str,
    db_session: Any = None,
) -> List[Dict[str, Any]]:
    """List all pending patch proposals for a project."""
    if not db_session:
        return []

    try:
        from sqlalchemy import select
        from database.models import PatchProposal

        stmt = select(PatchProposal).where(
            PatchProposal.project_id == project_id,
            PatchProposal.status == "awaiting_approval",
        ).order_by(PatchProposal.created_at.desc())

        result = db_session.execute(stmt)
        rows = result.scalars().all()

        return [
            {
                "id": r.id,
                "title": r.title,
                "source_agent": r.source_agent,
                "reason": r.reason,
                "status": r.status,
                "diff_preview": r.diff_text[:500],
                "created_at": str(r.created_at),
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"Could not list patches: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════
# DB Helpers
# ═══════════════════════════════════════════════════════════════════════


def _persist_proposal(proposal: PatchProposalData, db_session: Any) -> None:
    """Save patch proposal to DB."""
    try:
        from database.models import PatchProposal as PatchProposalModel

        diff_text = "\n---\n".join(d.unified_diff for d in proposal.diffs)
        db_proposal = PatchProposalModel(
            id=proposal.id,
            project_id=proposal.project_id,
            organization_id="",
            user_id="",
            source_agent=proposal.source_agent,
            title=proposal.title,
            reason=proposal.reason,
            diff_text=diff_text,
            status=proposal.status,
            metadata_json=json.dumps({
                "files": [d.file_path for d in proposal.diffs],
                "lines_added": sum(d.lines_added for d in proposal.diffs),
                "lines_removed": sum(d.lines_removed for d in proposal.diffs),
            }),
        )
        db_session.add(db_proposal)
        db_session.commit()
    except Exception as e:
        logger.warning(f"Could not persist patch proposal: {e}")


def _load_proposal(patch_id: str, db_session: Any) -> Optional[PatchProposalData]:
    """Load a patch proposal from DB."""
    if not db_session:
        return None
    try:
        from database.models import PatchProposal as PatchProposalModel
        row = db_session.query(PatchProposalModel).filter(
            PatchProposalModel.id == patch_id
        ).first()
        if row:
            return PatchProposalData(
                id=row.id,
                project_id=row.project_id,
                source_agent=row.source_agent,
                title=row.title,
                reason=row.reason,
                status=row.status,
            )
    except Exception as e:
        logger.warning(f"Could not load patch: {e}")
    return None


def _update_proposal_status(patch_id: str, status: str, db_session: Any) -> None:
    """Update patch proposal status."""
    try:
        from database.models import PatchProposal as PatchProposalModel
        db_session.query(PatchProposalModel).filter(
            PatchProposalModel.id == patch_id
        ).update({"status": status})
        db_session.commit()
    except Exception as e:
        logger.warning(f"Could not update patch status: {e}")


def _json_dict(raw: str | None) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}
