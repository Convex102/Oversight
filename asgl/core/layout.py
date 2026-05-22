from typing import List, Dict, Any
import pandas as pd


class LayoutInterpreter:
    """Simple layout metadata: table bbox and header region."""

    def detect_regions(self, df: pd.DataFrame, header_depth: int = 1) -> List[Dict[str, Any]]:
        rows, cols = df.shape
        if rows == 0 or cols == 0:
            return []
        return [{
            "table_bbox": {
                "row_start": 0,
                "row_end": rows - 1,
                "col_start": 0,
                "col_end": cols - 1,
            },
            "header_rows": list(range(header_depth)),
            "body_row_start": header_depth,
            "n_rows": rows,
            "n_cols": cols,
        }]
