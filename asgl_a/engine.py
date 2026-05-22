from __future__ import annotations

from typing import Dict, Any, Optional
from pathlib import Path

from .config import ASGLAConfig
from .logging_utils import get_logger
from .debug import save_debug
from .statement_builder import StatementBuilder
from .kpis import KPIEngine
from .flags import FlagEngine
from .ml_risk import RiskModel
from .narrative import NarrativeRenderer

try:
    from .template_learning import TemplateRegistry, build_signature
except Exception:
    TemplateRegistry = None
    build_signature = None


class ASGLAEngine:
    """ASGL-A review engine with first-class template learning support."""

    def __init__(self, cfg: ASGLAConfig):
        self.cfg = cfg
        self.cfg.ensure_dirs()
        self.log = get_logger(self.cfg.output_root / "logs" / "asgl_a.log", level=self.cfg.log_level)
        self.builder = StatementBuilder()
        self.kpis = KPIEngine()
        self.flags = FlagEngine()
        self.risk = RiskModel(self.cfg) if self.cfg.enable_ml else None
        self.narr = NarrativeRenderer() if self.cfg.enable_llm else None

        self.registry = None
        if self.cfg.enable_template_learning and TemplateRegistry is not None and self.cfg.template_registry_path is not None:
            try:
                self.registry = TemplateRegistry(self.cfg.template_registry_path)
            except Exception as e:
                self.log.warning("Template registry init failed: %s", e)

    def _apply_template_overrides(self, df, asglc_schema: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
        """Apply stored overrides from a TemplateProfile to schema."""
        if not profile:
            return asglc_schema
        roles = asglc_schema.get("roles", {}) if isinstance(asglc_schema, dict) else {}
        overrides = profile.get("column_role_overrides") or {}
        for col, role in overrides.items():
            if col in roles:
                roles[col] = role
        asglc_schema["roles"] = roles
        asglc_schema.setdefault("template", {})["profile_id"] = profile.get("id")
        return asglc_schema

    def run(
        self,
        df,
        asglc_schema: Dict[str, Any],
        meta: Optional[Dict[str, Any]] = None,
        *,
        tenant_id: str = "default",
        doc_type: str = "working_papers",
        field_selections: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        meta = meta or {}
        template_info: Dict[str, Any] = {"enabled": bool(self.registry is not None)}

        try:
            # --- Template learning ---
            if self.registry is not None and build_signature is not None:
                sig = build_signature(df, asglc_schema)
                match = self.registry.match(tenant_id=tenant_id, doc_type=doc_type, sig=sig)
                m = match.get("match")
                similarity = float(match.get("similarity", 0.0))
                drift = float(match.get("drift", 1.0))

                profile = None
                if m is not None:
                    profile = m.to_record()

                # Confidence gating: require confirmation if similarity is low
                needs_confirmation = similarity < float(self.cfg.template_require_confirmation_below)

                # Upsert using best evidence (field selections from user override)
                # Note: this is safe because registry is append-only and versioned by drift.
                upserted = self.registry.upsert_from_signature(
                    tenant_id=tenant_id,
                    doc_type=doc_type,
                    sig=sig,
                    field_selections=field_selections or (profile.get("field_selections") if profile else None),
                    drift_threshold=float(self.cfg.template_drift_new_version_threshold),
                )
                template_info.update({
                    "matched_profile_id": profile.get("id") if profile else None,
                    "active_profile_id": upserted.id,
                    "similarity": similarity,
                    "drift": drift,
                    "needs_confirmation": needs_confirmation,
                    "version": upserted.version,
                    "parent_id": upserted.parent_id,
                })

                # Apply stored overrides (if any) to schema
                asglc_schema = self._apply_template_overrides(df, asglc_schema, upserted.to_record())

            self.log.info("ASGL-A start (rows=%s cols=%s)", len(df), len(df.columns))
            statements = self.builder.build(df, asglc_schema)
            kpis = self.kpis.compute(statements)
            flags = self.flags.generate(statements, kpis)

            ml_out = {}
            if self.risk is not None:
                ml_out = self.risk.score(statements, kpis, flags)

            facts = {
                "period": meta.get("period", {}),
                "currency": meta.get("currency", "GBP"),
                "confidence": {
                    "schema": float(asglc_schema.get("metadata", {}).get("confidence", 0.0) or 0.0) if isinstance(asglc_schema, dict) else 0.0,
                    "statements": float(statements.get("confidence", 0.0) or 0.0) if isinstance(statements, dict) else 0.0,
                    "template_match": float(template_info.get("similarity", 0.0) or 0.0),
                },
                "template": template_info,
                "pnl": statements.get("pnl", {}),
                "balance_sheet": statements.get("balance_sheet", {}),
                "kpis": kpis,
                "flags": flags,
                "ml": ml_out,
                "disclaimer": "Decision support only; professional judgement required."
            }

            narrative = None
            if self.narr is not None:
                narrative = self.narr.render(facts)

            debug_path = save_debug(
                self.cfg.output_root / "debug",
                {"facts": facts, "statements": statements, "kpis": kpis, "flags": flags, "ml": ml_out},
                prefix="asgl_a_review",
            )

            out = {
                "template": template_info,
                "statements": statements,
                "kpis": kpis,
                "flags": flags,
                "ml": ml_out,
                "narrative": narrative,
                "debug_path": str(debug_path),
            }
            return out

        except Exception as e:
            self.log.exception("ASGL-A failed: %s", e)
            raise
