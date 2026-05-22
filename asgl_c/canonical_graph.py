from dataclasses import dataclass, field
from typing import Dict, List

@dataclass
class GraphNode:
    id: str
    type: str
    attributes: dict
    confidence: float

@dataclass
class GraphEdge:
    source: str
    target: str
    relation: str
    evidence: list
    confidence: float

@dataclass
class CanonicalDecisionGraph:
    nodes: Dict[str, GraphNode] = field(default_factory=dict)
    edges: List[GraphEdge] = field(default_factory=list)
    constraints: list = field(default_factory=list)
    uncertainty: dict = field(default_factory=dict)

    def add_node(self, node: GraphNode):
        self.nodes[node.id] = node

    def add_edge(self, edge: GraphEdge):
        self.edges.append(edge)
