import sys
import os
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services.mental_model import slang_analyzer
from services.mental_model.slang_analyzer import (
    SlangStructuralAnalyzer,
    _build_symbol_table,
    _normalize_slang_ast,
)


def test_slang_ast_normalizer_extracts_interfaces_modports_and_hierarchy(tmp_path: Path):
    ast = {
        "definitions": [
            {
                "kind": "Definition",
                "definitionKind": "Interface",
                "name": "bus_if",
                "members": [
                    {"kind": "Port", "name": "clk", "direction": "input", "type": "logic"},
                    {"kind": "Port", "name": "data", "direction": "inout", "type": "logic[7:0]"},
                    {
                        "kind": "Modport",
                        "name": "master",
                        "ports": [{"name": "clk"}, {"name": "data"}],
                    },
                ],
            }
        ],
        "design": {
            "members": [
                {
                    "kind": "Instance",
                    "name": "u_top",
                    "body": {
                        "definitionKind": "Module",
                        "name": "top",
                        "members": [
                            {"kind": "Port", "name": "clk", "direction": "input", "type": "logic"},
                            {
                                "kind": "Instance",
                                "name": "u_child",
                                "body": {
                                    "definitionKind": "Module",
                                    "name": "child",
                                    "members": [
                                        {
                                            "kind": "Port",
                                            "name": "data_o",
                                            "direction": "output",
                                            "type": "logic[3:0]",
                                        }
                                    ],
                                },
                            },
                        ],
                    },
                }
            ]
        },
    }

    module_index = _normalize_slang_ast(ast, tmp_path)
    assert {"top", "child", "bus_if"}.issubset(module_index.keys())
    assert module_index["top"]["instantiations"][0]["module"] == "child"
    assert module_index["child"]["ports"][0]["width"] == 4
    assert module_index["bus_if"]["definition_kind"] == "Interface"
    assert module_index["bus_if"]["modports"][0]["name"] == "master"

    symbols = _build_symbol_table(module_index)
    assert "data" in symbols["bus_if"]
    assert "master" in symbols["bus_if"]


def test_slang_discovery_checks_packaged_runtime_bin(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CHIPVERIFY_SLANG_BIN", raising=False)
    monkeypatch.setenv("CHIPVERIFY_RESOURCES_DIR", str(tmp_path))
    monkeypatch.setattr(slang_analyzer.shutil, "which", lambda _name: None)

    exe_name = "slang.exe" if os.name == "nt" else "slang"
    bundled = tmp_path / "runtime" / "bin" / exe_name
    bundled.parent.mkdir(parents=True)
    bundled.write_text("", encoding="utf-8")

    assert SlangStructuralAnalyzer.discover_slang_binary() == str(bundled.resolve())
