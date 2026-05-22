from .canonical_graph import CanonicalDecisionGraph
from .table_inference import TableInferenceEngine
from .relationship_graph import RelationshipGraphBuilder
from .feature_compiler import FeatureCompiler
from .confidence_engine import ConfidenceEngine

def compile_semantic_graph(df, config, table_id="table_0"):
    graph = CanonicalDecisionGraph()
    node = TableInferenceEngine(config).infer(df, table_id)
    graph.add_node(node)
    graph.edges.extend(RelationshipGraphBuilder().build(graph.nodes))
    ConfidenceEngine().propagate(graph)
    X = FeatureCompiler().compile(df, graph)
    return graph, X
