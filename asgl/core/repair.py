import pandas as pd


class RepairEngine:
    """Basic structure repair: drop all-null rows/cols, ffill/bfill."""

    def repair(self, df: pd.DataFrame) -> pd.DataFrame:
        df2 = df.copy()
        df2 = df2.dropna(how="all")
        df2 = df2.dropna(how="all", axis=1)
        df2 = df2.fillna(method="ffill").fillna(method="bfill")
        return df2
