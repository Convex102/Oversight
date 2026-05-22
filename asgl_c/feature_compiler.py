import pandas as pd

class FeatureCompiler:
    def compile(self, df: pd.DataFrame, graph):
        X = pd.DataFrame(index=df.index)
        for node in graph.nodes.values():
            if node.type == "table":
                for col, (role, _) in node.attributes["roles"].items():
                    if role == "value" and col in df.columns:
                        X[f"{node.id}::{col}_delta"] = df[col].diff()
        return X
