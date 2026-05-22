from __future__ import annotations

from typing import Any, Dict, Optional

from .events import FlywheelEventType, make_event
from .dataset import DatasetStore
from .queue import ActiveLearningQueue
from .trainer import TrainingScheduler


class FlywheelEngine:
    def __init__(self, *, tenant_id: str, client_id: str, run_id: str, vertical: str, logger=None, telemetry=None):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.run_id = run_id
        self.vertical = vertical
        self.logger = logger
        self.telemetry = telemetry

        self.dataset = DatasetStore(logger=logger)
        self.queue = ActiveLearningQueue(logger=logger)
        self.trainer = TrainingScheduler(logger=logger)

    def capture(self, *, type: FlywheelEventType, actor: str, confidence: float, payload: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        evt = make_event(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            run_id=self.run_id,
            type=type,
            actor=actor,
            vertical=self.vertical,
            confidence=confidence,
            payload=payload,
            meta=meta,
        )
        try:
            self.dataset.append(tenant_id=self.tenant_id, vertical=self.vertical, row=evt)
        except Exception:
            if self.logger:
                self.logger.exception("flywheel.dataset_append_failed")

        try:
            if self.telemetry is not None:
                fn = getattr(self.telemetry, "emit", None)
                if callable(fn):
                    fn("flywheel.event", evt)
        except Exception:
            pass
        return evt

    def maybe_queue_active_learning(self, *, kind: str, confidence: float, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            impact = float(payload.get("impact") or 0.5)
            priority = (1.0 - float(confidence)) * 0.7 + impact * 0.3
            if confidence < 0.75 or impact >= 0.8:
                qi = self.queue.add(
                    tenant_id=self.tenant_id,
                    client_id=self.client_id,
                    run_id=self.run_id,
                    vertical=self.vertical,
                    kind=kind,
                    priority=priority,
                    confidence=confidence,
                    payload=payload,
                )
                self.capture(
                    type=FlywheelEventType.QUEUE_ITEM_CREATED,
                    actor="system",
                    confidence=confidence,
                    payload={"queue_item": qi.to_dict()},
                    meta={"kind": kind, "priority": priority},
                )
                return qi.to_dict()
        except Exception:
            if self.logger:
                self.logger.exception("flywheel.queue_failed")
        return None

    def health(self) -> Dict[str, Any]:
        ds = self.dataset.snapshot_meta(tenant_id=self.tenant_id, vertical=self.vertical)
        qs = self.queue.stats(tenant_id=self.tenant_id, vertical=self.vertical)
        tj = self.trainer.list(tenant_id=self.tenant_id, vertical=self.vertical)[:5]
        return {"dataset": ds, "queue": qs, "recent_training": tj}

    def maybe_trigger_training(self, *, min_new_rows: int = 200, model_id: str = "di_mapper", base_version: str = "v0") -> Optional[Dict[str, Any]]:
        try:
            meta = self.dataset.snapshot_meta(tenant_id=self.tenant_id, vertical=self.vertical)
            if int(meta.get("rows") or 0) >= int(min_new_rows):
                job = self.trainer.trigger(
                    tenant_id=self.tenant_id,
                    vertical=self.vertical,
                    model_id=model_id,
                    base_version=base_version,
                    reason=f"dataset_rows>={min_new_rows}",
                    meta={"dataset_meta": meta},
                )
                self.capture(type=FlywheelEventType.TRAINING_TRIGGERED, actor="system", confidence=1.0, payload={"training_job": job.to_dict()})
                return job.to_dict()
        except Exception:
            if self.logger:
                self.logger.exception("flywheel.training_trigger_failed")
        return None
