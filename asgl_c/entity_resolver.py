from .canonical_graph import GraphNode, GraphEdge

class EntityResolver:
    def resolve(self, graph):
        for node in list(graph.nodes.values()):
            if node.type == "table":
                ent = GraphNode(id=f"entity::{node.id}", type="entity",
                                attributes={"source": node.id}, confidence=node.confidence)
                graph.add_node(ent)
                graph.add_edge(GraphEdge(node.id, ent.id, "represents", ["table->entity"], node.confidence))
