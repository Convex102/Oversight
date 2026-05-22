from typing import Dict
import pandas as pd


class SpreadsheetParser:
    """Multi-sheet Excel parser."""

    def __init__(self, file_path: str):
        self.file_path = file_path

    def load(self) -> Dict[str, pd.DataFrame]:
        xl = pd.ExcelFile(self.file_path)
        return {s: xl.parse(s, header=None) for s in xl.sheet_names}
