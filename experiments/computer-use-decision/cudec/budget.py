"""Request budget with a persistent log (hard caps across all processes)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

CAPS = {"decision": 400, "llm": 200}


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    """Counts every attempted request (successful or not) and persists them to a JSONL log."""

    def __init__(self, path: Path, caps: dict[str, int] | None = None):
        self.path = Path(path)
        self.caps = dict(caps or CAPS)
        self.lock = threading.Lock()
        self.counts = {kind: 0 for kind in self.caps}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    kind = json.loads(line).get("kind")
                    if kind in self.counts:
                        self.counts[kind] += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def reserve(self, kind: str, **meta) -> None:
        with self.lock:
            if self.counts[kind] >= self.caps[kind]:
                raise BudgetExceeded(f"{kind} request cap {self.caps[kind]} reached")
            self.counts[kind] += 1
            record = {"ts": round(time.time(), 3), "kind": kind, "n": self.counts[kind], **meta}
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")

    def remaining(self, kind: str) -> int:
        return self.caps[kind] - self.counts[kind]
