"""Tests for SystemVerilog stop-condition trie."""

from __future__ import annotations

from services.completion.stop_conditions import create_stop_condition


def test_stops_at_endmodule():
    stop = create_stop_condition("systemverilog")
    text = "  assign x = 1;\nendmodule\nmodule other"
    trimmed = stop.apply(text)
    assert trimmed == "  assign x = 1;"


def test_stops_at_double_newline():
    stop = create_stop_condition("systemverilog")
    text = "foo();\n\nmore code"
    trimmed = stop.apply(text)
    assert trimmed == "foo();"


def test_fim_sentinel_trim():
    stop = create_stop_condition("systemverilog")
    text = "logic a;<MID>extra"
    trimmed = stop.apply(text)
    assert "<MID>" not in trimmed
