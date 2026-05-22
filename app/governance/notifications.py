from __future__ import annotations

from typing import Any, Dict


def notify(logger, channel: str, payload: Dict[str, Any]) -> None:
    if logger:
        try:
            logger.warning("governance.notify", extra={"channel": channel, "payload": payload})
        except Exception:
            pass


def notify_policy_block(logger, *, tenant_id: str, client_id: str, run_id: str, policy_state: Dict[str, Any]) -> None:
    notify(logger, "policy_block", {"tenant_id": tenant_id, "client_id": client_id, "run_id": run_id, "state": policy_state})


def notify_incident(logger, *, incident: Dict[str, Any]) -> None:
    notify(logger, "incident", {"incident": incident})
