from __future__ import annotations

from .queue import ActiveLearningQueue
from .persistence import JSONFileStore


class PersistentActiveLearningQueue(ActiveLearningQueue):
    def __init__(self, *, path: str = "data/flywheel/queue_state.json", logger=None):
        super().__init__(logger=logger)
        self._store = JSONFileStore(path, logger=logger)
        self._load()

    def _load(self) -> None:
        doc = self._store.load() or {}
        items = doc.get("items") or {}
        heap = doc.get("heap") or []
        try:
            from .queue import QueueItem
            self._items = {k: QueueItem(**v) for k, v in items.items()}
            self._heap = [(float(p), str(i)) for p, i in heap]
        except Exception:
            self._items = {}
            self._heap = []

    def _save(self) -> None:
        try:
            items = {k: v.to_dict() for k, v in self._items.items()}
            doc = {"items": items, "heap": list(self._heap)}
            self._store.save(doc)
        except Exception:
            pass

    def add(self, *args, **kwargs):
        qi = super().add(*args, **kwargs)
        self._save()
        return qi

    def pop(self, *args, **kwargs):
        out = super().pop(*args, **kwargs)
        self._save()
        return out

    def resolve(self, *args, **kwargs):
        out = super().resolve(*args, **kwargs)
        self._save()
        return out
