import re
from typing import Dict, Any
import pandas as pd


class FormulaExtractor:
    """Detect formula-like cells and simple reference patterns."""

    CELL_REF_RE = re.compile(r"\b[A-Z]{1,3}[0-9]{1,7}\b")

    def extract(self, df: pd.DataFrame) -> Dict[str, Any]:
        formula_cells = 0
        referenced_tokens = []
        for r in range(df.shape[0]):
            for c in range(df.shape[1]):
                val = df.iat[r, c]
                if isinstance(val, str) and val.strip().startswith("="):
                    formula_cells += 1
                    referenced_tokens.extend(self.CELL_REF_RE.findall(val))
        return {
            "formula_cells": int(formula_cells),
            "unique_reference_like_tokens": sorted(set(referenced_tokens))[:100],
        }
