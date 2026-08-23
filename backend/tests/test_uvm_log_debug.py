import hashlib
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from database.models import ProjectArtifact
from services.verification.uvm_log_parser import parse_uvm_logs
from services.verification.uvm_log_repair import build_uvm_repair_candidates


CADENCE_LOG = """
xmvlog: *E,NOPBIND (testbench.sv,8|26): Package cfs_aligner_core_pkg could not be bound.
xmvlog: *E,NOPBIND (apb_sequencer.sv,13|13): Package apb_pkg could not be bound.
xmvlog: *E,NOIPRT (apb_sequencer.sv,36|15): Unrecognized declaration 'apb_agent_config' of unknown type.
xmvlog: *E,NOIPRT (cfs_aligner_core_virtual_sequencer.sv,39|19): Unrecognized declaration 'apb_master_sequencer' of unknown type.
xmvlog: *E,NOIPRT (cfs_aligner_core_virtual_sequencer.sv,42|21): Unrecognized declaration 'md_rx_master_sequencer' of unknown type.
xmvlog: *E,NOIPRT (cfs_aligner_core_virtual_sequencer.sv,45|20): Unrecognized declaration 'md_tx_slave_sequencer' of unknown type.
xmvlog: *E,SVNOTY (apb_sequence_item.sv,1|48): Syntactically this identifier appears to begin a datatype but it does not refer to a visible datatype in the current scope.
xmvlog: *E,CLSSPX : 'super' can only be used within a class scope that derives from a base class.
xmvlog: *E,VLGERR: An error occurred during parsing.
"""


def _artifact(tmp_path: Path, name: str, content: str) -> ProjectArtifact:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    raw = content.encode("utf-8")
    return ProjectArtifact(
        id=f"id-{name}",
        organization_id="org",
        project_id="project",
        user_id="user",
        artifact_type="generated",
        revision=1,
        source="test",
        file_path=str(path),
        checksum_sha256=hashlib.sha256(raw).hexdigest(),
        filename=name,
        content_type="text/plain",
        size_bytes=len(raw),
    )


def test_parse_cadence_uvm_log_groups_known_errors():
    analysis = parse_uvm_logs([CADENCE_LOG], simulator="auto")

    assert analysis.tool == "xcelium"
    assert analysis.status == "failed"
    codes = {diag.code for diag in analysis.diagnostics}
    assert {"NOPBIND", "NOIPRT", "SVNOTY", "CLSSPX", "VLGERR"} <= codes
    causes = {cause.category for cause in analysis.root_causes}
    assert "missing_package_or_compile_order" in causes
    assert "llm_introduced_unknown_package" in causes
    assert "llm_introduced_unknown_class" in causes
    assert "uvm_dependency_not_visible" in causes
    assert "cascade_parse_error" in causes


def test_parse_questa_and_vcs_package_errors():
    questa = """
** Error: (apb_sequencer.sv(13)): vlog-13069: Package apb_pkg could not be bound.
"""
    vcs = """
vlogan: compiling generated UVM
Error-[SV-LCM-PND] Package apb_pkg not defined
"""

    questa_analysis = parse_uvm_logs([questa], simulator="auto")
    vcs_analysis = parse_uvm_logs([vcs], simulator="auto")

    assert questa_analysis.tool == "questa"
    assert questa_analysis.diagnostics[0].code == "NOPBIND"
    assert questa_analysis.diagnostics[0].symbol == "apb_pkg"
    assert vcs_analysis.tool == "vcs"
    assert vcs_analysis.diagnostics[0].code == "NOPBIND"
    assert vcs_analysis.diagnostics[0].symbol == "apb_pkg"


def test_repair_candidates_remove_unknown_package_and_class_aliases(tmp_path):
    analysis = parse_uvm_logs([CADENCE_LOG], simulator="xcelium")
    artifacts = [
        _artifact(
            tmp_path,
            "cfs_aligner_core_pkg.sv",
            """package cfs_aligner_core_pkg;
class apb_sequence_item extends uvm_sequence_item; endclass
class md_rx_sequence_item extends uvm_sequence_item; endclass
class md_tx_sequence_item extends uvm_sequence_item; endclass
endpackage
""",
        ),
        _artifact(
            tmp_path,
            "apb_sequencer.sv",
            """import uvm_pkg::*;
`include "uvm_macros.svh"
import apb_pkg::*;
class apb_sequencer extends uvm_sequencer #(apb_sequence_item);
  `uvm_component_utils(apb_sequencer)
  apb_agent_config m_cfg;
  function new(string name = "apb_sequencer", uvm_component parent = null);
    super.new(name, parent);
  endfunction
  virtual function void build_phase(uvm_phase phase);
    super.build_phase(phase);
    if (!uvm_config_db#(apb_agent_config)::get(this, "", "m_cfg", m_cfg)) begin
      `uvm_fatal(get_type_name(), "Failed to get config")
    end
  endfunction
endclass
""",
        ),
        _artifact(
            tmp_path,
            "cfs_aligner_core_virtual_sequencer.sv",
            """import uvm_pkg::*;
`include "uvm_macros.svh"
class apb_sequencer extends uvm_sequencer #(apb_sequence_item); endclass
class md_rx_sequencer extends uvm_sequencer #(md_rx_sequence_item); endclass
class md_tx_sequencer extends uvm_sequencer #(md_tx_sequence_item); endclass
class cfs_aligner_core_virtual_sequencer extends uvm_sequencer;
  apb_master_sequencer apb_sqr;
  md_rx_master_sequencer md_rx_sqr;
  md_tx_slave_sequencer md_tx_sqr;
endclass
""",
        ),
    ]

    candidates = build_uvm_repair_candidates(
        analysis=analysis,
        generated_artifacts=artifacts,
    )

    by_file = {candidate.artifact.filename: candidate for candidate in candidates}
    assert "apb_sequencer.sv" in by_file
    assert "import apb_pkg::*;" not in by_file["apb_sequencer.sv"].proposed_content
    assert "apb_agent_config" not in by_file["apb_sequencer.sv"].proposed_content
    assert "uvm_config_db" not in by_file["apb_sequencer.sv"].proposed_content
    assert "cfs_aligner_core_virtual_sequencer.sv" in by_file
    proposed = by_file["cfs_aligner_core_virtual_sequencer.sv"].proposed_content
    assert "apb_master_sequencer" not in proposed
    assert "md_rx_master_sequencer" not in proposed
    assert "md_tx_slave_sequencer" not in proposed
    assert "apb_sequencer apb_sqr;" in proposed
    assert "md_rx_sequencer md_rx_sqr;" in proposed
    assert "md_tx_sequencer md_tx_sqr;" in proposed
