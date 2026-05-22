from .canonical_graph import GraphEdge

class RelationshipGraphBuilder:
    def build(self, nodes):
        edges = []
        tables = [n for n in nodes.values() if n.type == "table"]
        for a in tables:
            for b in tables:
                if a.id != b.id:
                    if self._share_time(a, b):
                        edges.append(GraphEdge(a.id, b.id, "shared_time", ["common time"], 0.7))
        return edges

    def _share_time(self, a, b):
        ra = a.attributes.get("roles", {})
        rb = b.attributes.get("roles", {})
        return any(v[0]=="time" for v in ra.values()) and any(v[0]=="time" for v in rb.values())
