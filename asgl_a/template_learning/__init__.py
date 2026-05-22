"""Template learning for ASGL-A.

Goal:
- Accept arbitrary client working paper templates.
- Fingerprint and persist a template 'profile' (fields, mappings, review intent).
- Auto-match future uploads, detect drift, and version templates safely.
- Provide confidence gating: never silently apply a low-confidence mapping.
"""

from .registry import TemplateRegistry
from .fingerprint import TemplateSignature, build_signature
from .drift import drift_score
