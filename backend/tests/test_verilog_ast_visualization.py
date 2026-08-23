import shutil
import sys
from pathlib import Path

import graphviz
import pytest
from graphviz.backend import ExecutableNotFound

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.verilog_parser.parser import parse_verilog  # noqa: E402
from services.verilog_parser.visualizer import visualize_verilog_file  # noqa: E402


def _port_names(module_data: dict, direction: str) -> set[str]:
    return {
        port.get("name", "")
        for port in module_data.get("ports", [])
        if port.get("direction") == direction
    }


def test_parse_extracts_modules_ports_instances_and_assigns(tmp_path: Path):
    rtl_path = tmp_path / "dummy.v"
    rtl_path.write_text(
        """
        module and2(input a, input b, output y);
          assign y = a & b;
        endmodule

        module top(input a, input b, output y);
          wire n1;
          and2 u_and(.a(a), .b(b), .y(n1));
          assign y = n1;
        endmodule
        """,
        encoding="utf-8",
    )

    modules = parse_verilog(rtl_path)

    assert {"and2", "top"}.issubset(modules.keys())

    top = modules["top"]
    assert _port_names(top, "input") == {"a", "b"}
    assert _port_names(top, "output") == {"y"}

    instance_names = {
        instance["instance_name"] for instance in top.get("instances", [])
    }
    assert "u_and" in instance_names

    net_names = {net["name"] for net in top.get("nets", [])}
    assert "n1" in net_names

    assigns = {(item["lhs"], item["rhs"]) for item in top.get("assigns", [])}
    assert ("y", "n1") in assigns


def test_parse_counter_4bit_systemverilog_sample():
    rtl_path = BACKEND_ROOT / "original_core" / "examples" / "counter_4bit.sv"
    modules = parse_verilog(rtl_path)

    assert "counter_4bit" in modules
    counter = modules["counter_4bit"]

    assert {"clk", "rst_n", "enable"}.issubset(_port_names(counter, "input"))
    assert "count" in _port_names(counter, "output")


def test_visualizer_falls_back_to_dot_when_graphviz_binary_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rtl_path = BACKEND_ROOT / "original_core" / "examples" / "counter_4bit.sv"

    def _raise_missing_dot(self, *args, **kwargs):
        raise ExecutableNotFound("dot")

    monkeypatch.setattr(graphviz.Digraph, "render", _raise_missing_dot)

    generated = visualize_verilog_file(
        rtl_path, output_dir=tmp_path, output_format="svg"
    )

    assert "counter_4bit" in generated
    dot_path = Path(generated["counter_4bit"])
    assert dot_path.exists()
    assert dot_path.suffix == ".dot"


@pytest.mark.skipif(
    shutil.which("dot") is None, reason="Graphviz dot executable is not installed"
)
def test_visualizer_generates_svg_for_counter_4bit(tmp_path: Path):
    rtl_path = BACKEND_ROOT / "original_core" / "examples" / "counter_4bit.sv"

    generated = visualize_verilog_file(
        rtl_path, output_dir=tmp_path, output_format="svg"
    )

    assert "counter_4bit" in generated
    svg_path = Path(generated["counter_4bit"])
    assert svg_path.exists()
    assert svg_path.suffix == ".svg"
