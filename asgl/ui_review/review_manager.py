from typing import Dict, Any, List


class HumanReviewManager:
    """Prepare and apply human-in-the-loop semantic mapping review.

    - `build_review_payload` creates a light JSON structure summarising
      inferred mappings, scores and sample values.
    - `apply_review` merges human-corrected roles into the mapping.
    """

    def build_review_payload(
        self,
        sheet_name: str,
        table_index: int,
        df_head: Dict[str, List[Any]],
        mapping: Dict[str, str],
        scores: Dict[str, Dict[str, float]],
    ) -> Dict[str, Any]:
        columns_payload = []
        for col, role in mapping.items():
            columns_payload.append({
                "column_name": col,
                "proposed_role": role,
                "scores": scores.get(col, {}),
                "sample_values": df_head.get(col, [])[:5],
            })
        return {
            "sheet": sheet_name,
            "table_index": table_index,
            "columns": columns_payload,
        }

    def apply_review(
        self,
        original_mapping: Dict[str, str],
        review_overrides: Dict[str, str],
    ) -> Dict[str, str]:
        updated = dict(original_mapping)
        for col, role in review_overrides.items():
            if col in updated:
                updated[col] = role
        return updated
