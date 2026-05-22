"""ASGL-C: Semantic compiler layer.

Provides:
- basic table role inference (time/value/attribute)
- confidence propagation
- feature compilation for downstream ML
"""

from .config import ASGLCConfig
from .canonical_graph import GraphNode, GraphEdge, CanonicalDecisionGraph
from .integration import compile_semantic_graph

__all__ = ["ASGLCConfig","GraphNode","GraphEdge","CanonicalDecisionGraph","compile_semantic_graph"]
