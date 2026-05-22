from typing import Dict, Any
import pandas as pd


class DataDictionaryGenerator:
    def generate(self, df: pd.DataFrame, mapping: Dict[str, str]) -> Dict[str, Any]:
        col_types = df.dtypes.astype(str).to_dict()
        return {
            "columns": mapping,
            "types": col_types,
            "rows": int(len(df)),
        }
