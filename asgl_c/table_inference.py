import pandas as pd
from .canonical_graph import GraphNode
from .logging_utils import get_logger

class TableInferenceEngine:
    def __init__(self, config):
        self.log = get_logger("ASGLC.TableInference", config.log_dir / "asgl_c.log")

    def infer(self, df: pd.DataFrame, table_id: str):
        roles = {}
        for col in df.columns:
            s = df[col]
            if self._looks_like_time(s):
                roles[col] = ("time", 0.9)
            elif pd.api.types.is_numeric_dtype(s):
                roles[col] = ("value", 0.7)
            else:
                roles[col] = ("attribute", 0.5)
        node = GraphNode(id=table_id, type="table", attributes={"roles": roles},
                         confidence=sum(c for _, c in roles.values()) / len(roles))
        self.log.info(f"Inferred table {table_id} conf={node.confidence:.2f}")
        return node

    def _looks_like_time(self, s):
        try:
            return pd.to_datetime(s, errors="coerce").notna().mean() > 0.7
        except Exception:
            return False
