class ConfidenceEngine:
    def propagate(self, graph):
        if not graph.nodes:
            return
        base = min(n.confidence for n in graph.nodes.values())
        graph.uncertainty["schema"] = 1.0 - base
