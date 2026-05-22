from __future__ import annotations
import os, json
from typing import Dict, Any, Optional
import requests

SYSTEM_PROMPT = (
    "You are a senior UK accountant writing a professional review note for a set of accounts. "
    "Use ONLY the provided JSON facts. Do not introduce new numbers, causes, or assumptions. "
    "If information is missing, explicitly state that it is not available. "
    "Structure: Executive summary; P&L highlights; Balance sheet & liquidity; Key risks & checks; Disclaimer."
)

def _has_llm() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))

class NarrativeRenderer:
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.model = os.getenv("ASGLA_LLM_MODEL", "gpt-4.1-mini")
        self.temperature = float(os.getenv("ASGLA_LLM_TEMPERATURE", "0.1"))

    def render(self, facts: Dict[str, Any]) -> Optional[str]:
        if not _has_llm():
            return None
        # Use Chat Completions style compatible endpoint
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type":"application/json"}
        user_prompt = "Write the review note based on this JSON facts bundle:\n\n" + json.dumps(facts, indent=2, default=str)
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role":"system","content": SYSTEM_PROMPT},
                {"role":"user","content": user_prompt}
            ]
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=30)
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]
        except Exception:
            return None
