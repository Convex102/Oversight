from __future__ import annotations

from typing import List
import pandas as pd
from .schema import CanonicalSchema, RoleGroup

def validate_schema(df: pd.DataFrame, schema: CanonicalSchema) -> List[str]:
    warnings: List[str] = []

    if schema.time_column and schema.time_column.column not in df.columns:
        warnings.append("time_column not found in dataframe columns")

    for role in schema.all_roles():
        if role.column not in df.columns:
            warnings.append(f"role column missing: {role.column} ({role.group})")

    # Ensure we have at least one numeric role for modelling
    numeric_cols = []
    for role in list(schema.states.values()) + list(schema.flows.values()) + list(schema.drivers.values()):
        if role.column in df.columns and pd.api.types.is_numeric_dtype(df[role.column]):
            numeric_cols.append(role.column)
    if not numeric_cols:
        warnings.append("no numeric columns detected in states/flows/drivers; model routing may fail")

    # Encourage time column but not required
    if schema.time_column is None:
        warnings.append("no time column inferred; time-series features will be limited")

    return warnings
