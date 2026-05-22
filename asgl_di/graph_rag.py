from __future__ import annotations
from typing import Any, Dict, List, Tuple, Set
import re

def _tokenize(s: str) -> Set[str]:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9_\-\s]", " ", s)
    return {t for t in s.split() if len(t) >= 3}

def extract_relevant_subgraph(
    graph: Dict[str, Any],
    *,
    queries: List[str],
    max_nodes: int = 40,
    hop_depth: int = 2
) -> Dict[str, Any]:
    """Lightweight GraphRAG-style retrieval over an ASGL-C semantic graph."""
    if not isinstance(graph, dict):
        return {"nodes": [], "edges": [], "stats": {"reason": "graph_not_dict"}}

    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return {"nodes": [], "edges": [], "stats": {"reason": "invalid_schema"}}

    q_toks: Set[str] = set()
    for q in queries or []:
        q_toks |= _tokenize(str(q))

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        blob = f"{n.get('label','')} {n.get('text','')} {n.get('type','')}"
        n_toks = _tokenize(blob)
        if not n_toks or not q_toks:
            continue
        inter = len(n_toks & q_toks)
        if inter <= 0:
            continue
        score = inter / max(1, len(q_toks))
        scored.append((score, n))

    scored.sort(key=lambda x: x[0], reverse=True)
    seed_nodes = [n for _, n in scored[:max(10, max_nodes//4)]]

    # adjacency
    adj_out = {}
    adj_in = {}
    for e in edges:
        if not isinstance(e, dict):
            continue
        s = e.get("src"); d = e.get("dst")
        if s is None or d is None:
            continue
        adj_out.setdefault(s, []).append(d)
        adj_in.setdefault(d, []).append(s)

    selected = {n.get("id") for n in seed_nodes if isinstance(n, dict) and n.get("id") is not None}
    frontier = list(selected)

    for _ in range(hop_depth):
        new_ids = set()
        for nid in frontier:
            new_ids.update(adj_out.get(nid, []))
            new_ids.update(adj_in.get(nid, []))
        new_ids -= selected
        if not new_ids:
            break
        room = max_nodes - len(selected)
        for nid in list(new_ids)[:max(0, room)]:
            selected.add(nid)
        frontier = list(new_ids)[:max(0, room)]
        if len(selected) >= max_nodes:
            break

    id_to_node = {n.get("id"): n for n in nodes if isinstance(n, dict) and n.get("id") is not None}
    out_nodes = [id_to_node[i] for i in selected if i in id_to_node]
    sel = set(selected)
    out_edges = [e for e in edges if isinstance(e, dict) and e.get("src") in sel and e.get("dst") in sel]

    return {
        "nodes": out_nodes,
        "edges": out_edges,
        "stats": {"seed_count": len(seed_nodes), "selected_count": len(out_nodes), "edge_count": len(out_edges)}
    }
