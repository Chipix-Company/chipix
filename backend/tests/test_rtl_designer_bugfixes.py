from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RTL_DESIGNER_ROOT = REPO_ROOT / "RTL_designer"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(RTL_DESIGNER_ROOT) not in sys.path:
    sys.path.insert(0, str(RTL_DESIGNER_ROOT))

from RTL_designer.agents import nodes, prompts


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeLlm:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return _FakeResponse(self.content)


def test_complexity_parser_accepts_spacing_and_separator_variants():
    assert nodes._detect_complexity("COMPLEXITY: VERY LARGE") == "very_large"
    assert nodes._detect_complexity("Complexity: VERY-LARGE") == "very_large"
    assert nodes._detect_complexity("complexity:very_large") == "very_large"
    assert nodes._detect_complexity("COMPLEXITY : LARGE") == "large"
    assert nodes._detect_complexity("COMPLEXITY: MEDIUM") == "medium"


def test_submodule_parser_accepts_markdown_markers_and_sanitizes_names():
    decomposition = """
```markdown
***SUBMODULE***
### Module Name: **apb_bridge** - APB controller notes
### Functional Description
Bridge logic.
***END_SUBMODULE***

___ SUBMODULE ___
### Module Name: `bridge_top` -- integration notes
### Functional Description
Top-level logic.
___ END_SUBMODULE ___
```
"""

    specs = nodes._parse_submodule_specs(decomposition)

    assert [item["name"] for item in specs] == ["apb_bridge", "bridge_top"]
    assert all(
        item["name"].replace("_", "").isalnum()
        for item in specs
    )


def test_coder_prompts_preserve_spec_port_names_without_forced_prefixes():
    coder_prompts = (
        prompts.CODER_SYSTEM_PROMPT_VERILOG,
        prompts.CODER_SUBMODULE_PROMPT_VERILOG,
        prompts.CODER_SYSTEM_PROMPT_SV,
        prompts.CODER_SUBMODULE_PROMPT_SV,
    )

    for prompt in coder_prompts:
        assert "exact port names from the specification" in prompt
        assert "prefix: i_ for inputs" not in prompt
        assert "standard naming: i_ for inputs" not in prompt

    assert "do not reject code solely for prefix-style differences" in (
        prompts.REVIEWER_SYSTEM_PROMPT
    )


def test_hierarchical_revision_restarts_coder_and_rebuilds_accumulation(monkeypatch):
    fake_llm = _FakeLlm(
        "```verilog\nmodule leaf(input wire clk); endmodule\n```"
    )
    monkeypatch.setattr(nodes, "_get_llm", lambda temperature=0.1: fake_llm)

    result = nodes._coder_hierarchical(
        {
            "language": "verilog",
            "spec": "Original design specification",
            "submodule_specs": [
                {
                    "name": "leaf",
                    "spec": "### Module Name: leaf",
                    "code": "module leaf; // old\nendmodule",
                    "status": "generated",
                }
            ],
            "current_submodule_idx": 1,
            "accumulated_code": "STALE COMPOSED CODE",
            "revision_count": 1,
            "review_decision": "revise",
            "review": "Revise the leaf module.",
        }
    )

    assert len(fake_llm.calls) == 1
    assert result["current_submodule_idx"] == 1
    assert result["review_decision"] == ""
    assert "STALE COMPOSED CODE" not in result["accumulated_code"]
    assert "module leaf" in result["accumulated_code"]


def test_composer_receives_original_specification(monkeypatch):
    fake_llm = _FakeLlm("```verilog\nmodule top; endmodule\n```")
    monkeypatch.setattr(nodes, "_get_llm", lambda temperature=0.1: fake_llm)

    nodes.composer_node(
        {
            "spec": "Top must instantiate leaf with clk and rst_n.",
            "accumulated_code": "module leaf; endmodule",
        }
    )

    human_prompt = fake_llm.calls[0][1].content
    assert "## Original Specification" in human_prompt
    assert "Top must instantiate leaf with clk and rst_n." in human_prompt
    assert "module leaf; endmodule" in human_prompt
