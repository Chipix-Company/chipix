"""Regression coverage for the production-owned Graphify package."""

from pathlib import Path


def test_bundled_graphify_end_to_end(tmp_path: Path):
    from graphify.affected import format_affected, load_graph
    from graphify.analyze import god_nodes, suggest_questions, surprising_connections
    from graphify.build import build_from_json
    from graphify.cluster import cluster, score_all
    from graphify.detect import detect
    from graphify.export import to_html, to_json
    from graphify.extract import collect_files, extract
    from graphify.report import generate
    from graphify.serve import _find_node, _query_graph_text

    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "child.sv").write_text(
        "module child(input logic clk, output logic done); endmodule\n",
        encoding="utf-8",
    )
    (rtl / "top.sv").write_text(
        "module top(input logic clk); child u_child(.clk(clk), .done()); endmodule\n",
        encoding="utf-8",
    )

    extracted = extract(collect_files(rtl), cache_root=tmp_path / "cache", parallel=True)
    graph = build_from_json(extracted, root=str(rtl))
    communities = cluster(graph)
    cohesion = score_all(graph, communities)
    labels = {community_id: f"Community {community_id}" for community_id in communities}
    gods = god_nodes(graph)
    surprises = surprising_connections(graph, communities)
    questions = suggest_questions(graph, communities, labels)

    assert "rtl:module:top" in graph
    assert "rtl:module:child" in graph
    assert any(data.get("relation") == "instantiates" for _, _, data in graph.edges(data=True))
    assert _find_node(graph, "top") == ["rtl:module:top"]
    assert "child" in _query_graph_text(graph, "What does top instantiate?").lower()

    report = generate(
        graph,
        communities,
        cohesion,
        labels,
        gods,
        surprises,
        detect(rtl),
        token_cost={"estimated_tokens": 0},
        root=str(rtl),
        suggested_questions=questions,
    )
    assert "Codebase Graph Report" in report

    json_path = tmp_path / "graph.json"
    html_path = tmp_path / "graph.html"
    to_json(graph, communities, json_path, force=True)
    to_html(graph, communities, html_path, community_labels=labels)

    loaded = load_graph(json_path)
    assert loaded.number_of_nodes() == graph.number_of_nodes()
    assert "child" in format_affected(loaded, "top").lower()
    assert "https://" not in html_path.read_text(encoding="utf-8")


def test_packaging_has_no_research_graphify_dependency():
    repo_root = Path(__file__).resolve().parents[2]
    requirements = (repo_root / "backend" / "requirements.txt").read_text(encoding="utf-8")
    installer = (
        repo_root / "scripts" / "packaging" / "install_backend_pyinstaller_deps.sh"
    ).read_text(encoding="utf-8")
    linux_builder = (
        repo_root / "backend" / "runtime" / "linux" / "build_backend.sh"
    ).read_text(encoding="utf-8")
    windows_builder = (
        repo_root / "backend" / "runtime" / "windows" / "build_backend_exe.ps1"
    ).read_text(encoding="utf-8")

    assert "r_n_d/graphify" not in requirements.replace("\\", "/")
    assert "r_n_d/graphify" not in installer.replace("\\", "/")
    assert "--collect-submodules graphify" in linux_builder
    assert '"graphify"' in windows_builder
    assert "--self-test-graphify" in linux_builder
    assert "--self-test-graphify" in windows_builder
