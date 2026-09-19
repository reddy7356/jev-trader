from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, TextIO

from jev_trader.types import Action, JudgmentSet


class CalibrationLogger:
    """JSONL logger for (state, decision, outcome) triples.

    That triple is the calibration dataset. Every snapshot is logged with its
    decision, and every decision is labeled with the realized outcome after the
    configured horizon.
    """

    def __init__(self, path: Path, enabled: bool = True) -> None:
        self._path = path
        self._enabled = enabled
        self._file: TextIO | None = None
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("a", encoding="utf-8")

    def record_decision(
        self,
        *,
        block: int,
        ts: float,
        mid: float,
        state: dict[str, float],
        judgments: JudgmentSet | None,
        action: Action,
        latency_ms: float,
        source: str,
    ) -> str:
        decision_id = uuid.uuid4().hex[:12]
        if self._file is None:
            return decision_id
        record: dict[str, Any] = {
            "kind": "decision",
            "id": decision_id,
            "block": block,
            "ts": round(ts, 3),
            "mid": mid,
            "state": state,
            "judgments": asdict(judgments) if judgments is not None else None,
            "action": asdict(action),
            "latency_ms": round(latency_ms, 3),
            "source": source,
            "model": judgments.model if judgments is not None else None,
            "request_id": judgments.request_id if judgments is not None else None,
        }
        self._write(record)
        return decision_id

    def record_outcome(
        self,
        *,
        decision_id: str,
        horizon_blocks: int,
        mid_then: float,
        mid_now: float,
        outcome: str,
    ) -> None:
        if self._file is None:
            return
        self._write(
            {
                "kind": "outcome",
                "id": decision_id,
                "horizon_blocks": horizon_blocks,
                "mid_then": mid_then,
                "mid_now": mid_now,
                "outcome": outcome,
            }
        )

    def _write(self, record: dict[str, Any]) -> None:
        assert self._file is not None
        self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> CalibrationLogger:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
