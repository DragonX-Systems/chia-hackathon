"""Event logging for reproducible campaign summaries."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EventLogger:
    def __init__(self, path: Path | None, *, arm: str, trial_id: str,
                 retain_records: bool = True) -> None:
        self.path = path
        self.arm = arm
        self.trial_id = trial_id
        self._retain_records = retain_records
        self._sequence = 0
        self._started = time.perf_counter()
        self._started_epoch_seconds = time.time()
        self.records: list[dict[str, Any]] = []
        self._stream = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = path.open("w", encoding="utf-8")

    @property
    def elapsed_seconds(self) -> float:
        return time.perf_counter() - self._started

    @property
    def started_epoch_seconds(self) -> float:
        return self._started_epoch_seconds

    def emit(self, event_type: str, **fields: Any) -> dict[str, Any]:
        self._sequence += 1
        record = {
            "sequence": self._sequence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": self.elapsed_seconds,
            "arm": self.arm,
            "trial_id": self.trial_id,
            "event": event_type,
            **fields,
        }
        if self._retain_records:
            self.records.append(record)
        if self._stream is not None:
            self._stream.write(json.dumps(record, sort_keys=True) + "\n")
            self._stream.flush()
        return record

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def __enter__(self) -> "EventLogger":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()
