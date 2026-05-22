from typing import Dict, Any
import pandas as pd


class CellGraphEngine:
    """Grid-based adjacency approximation."""

    def build(self, df: pd.DataFrame) -> Dict[str, Any]:
        rows, cols = df.shape
        nodes = rows * cols
        horizontal_edges = rows * (cols - 1) if cols >= 2 else 0
        vertical_edges = cols * (rows - 1) if rows >= 2 else 0
        edges = horizontal_edges + vertical_edges
        return {
            "nodes": int(nodes),
            "edges": int(edges),
            "topology": "4-neighbour-grid",
        }
