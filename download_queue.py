"""Durable queue state. Only state transitions are written, not every progress tick."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import closing
from pathlib import Path

TERMINAL = {"completed", "failed", "skipped", "cancelled"}
RUNNABLE = {"pending", "interrupted"}
ACTIVE = {"downloading", "finalizing"}
LABELS = {
    "pending": "Pendente", "interrupted": "Interrompido", "downloading": "Baixando",
    "finalizing": "Finalizando", "completed": "Concluído", "failed": "Falhou",
    "skipped": "Indisponível", "cancelled": "Cancelado",
}


def queue_item(source: str, title: str, *, kind="ytdlp", **metadata) -> dict:
    return dict(id=uuid.uuid4().hex, source=source, title=title, kind=kind,
                status="pending", enabled=True, error="", files=[], **metadata)


class QueueRepository:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")

    def snapshot(self) -> dict:
        with self.lock, closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute("SELECT data FROM queue WHERE id=1").fetchone()
            if not row:
                return {"version": 1, "options": {}, "items": []}
            job = json.loads(row[0])
            if job.get("version") != 1 or not isinstance(job.get("items"), list):
                raise ValueError("Formato da fila não reconhecido. O arquivo foi preservado.")
            return job

    def _save(self, job):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("INSERT OR REPLACE INTO queue VALUES (1, ?)",
                               (json.dumps(job, ensure_ascii=False),))

    def replace(self, items: list[dict], options: dict, sources: list[str]):
        with self.lock:
            self._save({"version": 1, "options": options, "sources": sources, "items": items})

    def update(self, item_id: str, **changes):
        self.update_many({item_id: changes})

    def update_many(self, changes_by_id: dict):
        if not changes_by_id:
            return
        with self.lock:
            job = self.snapshot()
            known = {item["id"] for item in job["items"]}
            if changes_by_id.keys() - known:
                raise KeyError("Um dos itens não pertence à fila atual.")
            for item in job["items"]:
                item.update(changes_by_id.get(item["id"], {}))
            self._save(job)

    def recover(self):
        with self.lock:
            job = self.snapshot()
            for item in job["items"]:
                if item["status"] in ACTIVE:
                    item.update(status="interrupted", error="Sessão anterior interrompida; pronto para continuar.")
            self._save(job)
            return job


def queue_summary(items: list[dict], current_id: str | None = None, percent: float = 0) -> dict:
    selected = [item for item in items if item["enabled"]]
    done = sum(item["status"] in TERMINAL for item in selected)
    fraction = 0.0
    if any(item["id"] == current_id and item["status"] in ACTIVE for item in selected):
        # 100% received is not 100% finished: compatibility finalization is pending.
        fraction = max(0, min(99, percent)) / 100
    return {"total": len(selected), "done": done,
            "overall": (done + fraction) * 100 / len(selected) if selected else 0}
