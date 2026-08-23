"""
AGI Module 2 — Verification Knowledge Graph.

Persistent memory of design relationships:
  Signal -> Module -> Protocol -> Assertion -> Coverage -> Bug

Features:
  - Build graph from RTL analysis
  - Query relationships (e.g., "what depends on this signal?")
  - Store bug patterns for future designs
  - Design fingerprinting (similar designs share strategies)
  - Persists to disk as JSON
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from core.logger import get_logger

logger = get_logger("KnowledgeGraph")


@dataclass
class KGNode:
    """A node in the knowledge graph."""
    id: str
    kind: str          # module, signal, port, protocol, assertion, coverage, bug, test
    name: str
    properties: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass
class KGEdge:
    """An edge (relationship) in the knowledge graph."""
    source: str        # Node ID
    target: str        # Node ID
    relation: str      # has_port, depends_on, covers, tests, found_by, etc.
    properties: dict = field(default_factory=dict)


@dataclass
class BugPattern:
    """A recorded bug pattern for learning."""
    pattern_id: str
    description: str
    root_cause: str
    category: str         # timing, protocol, logic, etc.
    signals_involved: list[str] = field(default_factory=list)
    fix_template: str = ""
    occurrences: int = 1
    last_seen: float = field(default_factory=time.time)


class KnowledgeGraph:
    """Persistent verification knowledge graph."""

    def __init__(self, storage_path: str | Path = "output/knowledge") -> None:
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.nodes: dict[str, KGNode] = {}
        self.edges: list[KGEdge] = []
        self.bug_patterns: dict[str, BugPattern] = {}
        self._adjacency: dict[str, list[str]] = defaultdict(list)
        self._load()

    # ------------------------------------------------------------------
    # Node / Edge operations
    # ------------------------------------------------------------------

    def add_node(self, kind: str, name: str, **props) -> str:
        """Add a node and return its ID."""
        node_id = f"{kind}:{name}"
        if node_id not in self.nodes:
            self.nodes[node_id] = KGNode(id=node_id, kind=kind, name=name, properties=props)
        else:
            self.nodes[node_id].properties.update(props)
        return node_id

    def add_edge(self, source_id: str, target_id: str, relation: str, **props) -> None:
        """Add a relationship between two nodes."""
        edge = KGEdge(source=source_id, target=target_id, relation=relation, properties=props)
        self.edges.append(edge)
        self._adjacency[source_id].append(target_id)
        self._adjacency[target_id].append(source_id)

    def query_neighbors(self, node_id: str, relation: str = "") -> list[KGNode]:
        """Find all nodes connected to a given node."""
        neighbor_ids = self._adjacency.get(node_id, [])
        if relation:
            # Filter by relation type
            valid_targets = set()
            for edge in self.edges:
                if edge.relation == relation:
                    if edge.source == node_id:
                        valid_targets.add(edge.target)
                    elif edge.target == node_id:
                        valid_targets.add(edge.source)
            neighbor_ids = [n for n in neighbor_ids if n in valid_targets]
        return [self.nodes[n] for n in neighbor_ids if n in self.nodes]

    def query_by_kind(self, kind: str) -> list[KGNode]:
        """Get all nodes of a specific kind."""
        return [n for n in self.nodes.values() if n.kind == kind]

    # ------------------------------------------------------------------
    # Build from RTL analysis
    # ------------------------------------------------------------------

    def build_from_rtl(self, rtl_analysis) -> int:
        """Populate the graph from an RTL analysis object.

        Returns number of nodes added.
        """
        count = 0

        # Add module node
        mod_id = self.add_node("module", rtl_analysis.top_module)
        count += 1

        # Add ports
        for name, port in rtl_analysis.port_map.items():
            port_id = self.add_node(
                "port", name,
                direction=port.direction.value,
                width=port.width,
            )
            self.add_edge(mod_id, port_id, "has_port")
            count += 1

        # Add internal signals
        for sig in rtl_analysis.internal_signals:
            sig_id = self.add_node("signal", sig.name, width=sig.width)
            self.add_edge(mod_id, sig_id, "has_signal")
            count += 1

        # Add FSM states
        for state in rtl_analysis.fsm_states:
            state_id = self.add_node("state", state)
            self.add_edge(mod_id, state_id, "has_state")
            count += 1

        # Add instantiations
        for inst in rtl_analysis.instantiations:
            inst_id = self.add_node("instance", inst)
            self.add_edge(mod_id, inst_id, "instantiates")
            count += 1

        logger.info("Knowledge graph: %d nodes from RTL", count)
        return count

    # ------------------------------------------------------------------
    # Bug pattern learning
    # ------------------------------------------------------------------

    def record_bug(
        self,
        description: str,
        root_cause: str,
        category: str,
        signals: list[str] = None,
        fix: str = "",
    ) -> str:
        """Record a bug pattern for future reference."""
        import hashlib
        pattern_id = hashlib.md5(description.encode()).hexdigest()[:8]

        if pattern_id in self.bug_patterns:
            self.bug_patterns[pattern_id].occurrences += 1
            self.bug_patterns[pattern_id].last_seen = time.time()
        else:
            self.bug_patterns[pattern_id] = BugPattern(
                pattern_id=pattern_id,
                description=description,
                root_cause=root_cause,
                category=category,
                signals_involved=signals or [],
                fix_template=fix,
            )

        # Add to graph
        bug_id = self.add_node("bug", pattern_id, description=description, category=category)
        for sig in (signals or []):
            sig_id = f"signal:{sig}"
            if sig_id in self.nodes:
                self.add_edge(bug_id, sig_id, "involves")

        self._save()
        return pattern_id

    def find_similar_bugs(self, description: str) -> list[BugPattern]:
        """Find previously seen bugs similar to a description."""
        desc_lower = description.lower()
        matches = []
        for pattern in self.bug_patterns.values():
            # Simple keyword matching
            pattern_words = set(pattern.description.lower().split())
            desc_words = set(desc_lower.split())
            overlap = len(pattern_words & desc_words)
            if overlap >= 2:
                matches.append(pattern)
        return sorted(matches, key=lambda p: p.occurrences, reverse=True)

    # ------------------------------------------------------------------
    # Design fingerprinting
    # ------------------------------------------------------------------

    def get_design_fingerprint(self) -> dict:
        """Generate a fingerprint of the current design for similarity matching."""
        modules = self.query_by_kind("module")
        ports = self.query_by_kind("port")
        signals = self.query_by_kind("signal")
        states = self.query_by_kind("state")

        return {
            "module_count": len(modules),
            "port_count": len(ports),
            "signal_count": len(signals),
            "has_fsm": len(states) > 0,
            "state_count": len(states),
            "port_names": sorted(n.name for n in ports),
            "protocols": [
                n.name for n in self.query_by_kind("protocol")
            ],
        }

    # ------------------------------------------------------------------
    # Context generation for LLM
    # ------------------------------------------------------------------

    def generate_context(self, max_tokens: int = 2000) -> str:
        """Generate a context string for LLM consumption."""
        lines = ["KNOWLEDGE GRAPH CONTEXT:"]

        # Modules
        modules = self.query_by_kind("module")
        for mod in modules:
            lines.append(f"  Module: {mod.name}")
            ports = self.query_neighbors(mod.id, "has_port")
            for port in ports[:20]:
                props = port.properties
                lines.append(
                    f"    Port: {port.name} ({props.get('direction', '?')}) "
                    f"[{props.get('width', 1)}-bit]"
                )

        # Bug patterns
        if self.bug_patterns:
            lines.append(f"\n  Known bug patterns ({len(self.bug_patterns)}):")
            for bp in sorted(self.bug_patterns.values(),
                             key=lambda p: p.occurrences, reverse=True)[:5]:
                lines.append(f"    [{bp.category}] {bp.description} (seen {bp.occurrences}x)")

        result = "\n".join(lines)
        return result[:max_tokens]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save(self) -> None:
        """Save graph to disk."""
        data = {
            "nodes": {
                nid: {"kind": n.kind, "name": n.name, "properties": n.properties}
                for nid, n in self.nodes.items()
            },
            "edges": [
                {"source": e.source, "target": e.target, "relation": e.relation}
                for e in self.edges
            ],
            "bugs": {
                pid: {
                    "description": p.description,
                    "root_cause": p.root_cause,
                    "category": p.category,
                    "signals": p.signals_involved,
                    "fix": p.fix_template,
                    "occurrences": p.occurrences,
                }
                for pid, p in self.bug_patterns.items()
            },
        }
        path = self.storage_path / "knowledge_graph.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load(self) -> None:
        """Load graph from disk."""
        path = self.storage_path / "knowledge_graph.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for nid, ndata in data.get("nodes", {}).items():
                self.nodes[nid] = KGNode(
                    id=nid, kind=ndata["kind"],
                    name=ndata["name"], properties=ndata.get("properties", {}),
                )
            for edata in data.get("edges", []):
                self.add_edge(edata["source"], edata["target"], edata["relation"])
            for pid, pdata in data.get("bugs", {}).items():
                self.bug_patterns[pid] = BugPattern(
                    pattern_id=pid, description=pdata["description"],
                    root_cause=pdata["root_cause"], category=pdata["category"],
                    signals_involved=pdata.get("signals", []),
                    fix_template=pdata.get("fix", ""),
                    occurrences=pdata.get("occurrences", 1),
                )
            logger.info("Loaded knowledge graph: %d nodes, %d edges, %d bugs",
                        len(self.nodes), len(self.edges), len(self.bug_patterns))
        except Exception as e:
            logger.warning("Failed to load knowledge graph: %s", e)

    @property
    def summary(self) -> str:
        kinds = defaultdict(int)
        for n in self.nodes.values():
            kinds[n.kind] += 1
        kind_str = ", ".join(f"{k}:{v}" for k, v in kinds.items())
        return f"KG: {len(self.nodes)} nodes ({kind_str}), {len(self.edges)} edges, {len(self.bug_patterns)} bug patterns"
