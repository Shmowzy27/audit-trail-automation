from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class ProcessingState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = {"processed_messages": {}}

    def is_processed(self, message_id: str) -> bool:
        return message_id in self.data["processed_messages"]

    def mark_processed(self, message_id: str, result: dict) -> None:
        self.data["processed_messages"][message_id] = {
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "result": result,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        temporary.replace(self.path)
