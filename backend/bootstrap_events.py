"""Incremental parser for the versioned KACE bootstrap event stream."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Callable, Dict, Optional


PROTOCOL = "kace-bootstrap/v1"
EVENT_PREFIX = "=== KACE_BOOTSTRAP_EVENT:"
TERMINAL_EVENTS = frozenset({
    "workflow_succeeded",
    "workflow_failed",
    "workflow_cancelled",
})
ALLOWED_EVENTS = TERMINAL_EVENTS | {"workflow_started", "stage_started"}
_EVENT_RE = re.compile(r"=== KACE_BOOTSTRAP_EVENT:\s*(\{[^\r\n]*\})\s*===")


@dataclass(frozen=True)
class BootstrapCursor:
    workflow_id: str
    sequence: int
    event: dict


class BootstrapEventParser:
    """Validate and deduplicate bootstrap events from arbitrary SSH chunks."""

    MAX_PARTIAL_LINE = 64 * 1024

    def __init__(self, on_event: Optional[Callable[[dict], None]] = None):
        self._on_event = on_event
        self._partial = ""
        self._workflows: Dict[str, BootstrapCursor] = {}
        self._lock = threading.Lock()

    @property
    def workflows(self) -> dict[str, BootstrapCursor]:
        with self._lock:
            return dict(self._workflows)

    def feed(self, data: str) -> list[dict]:
        if not isinstance(data, str) or not data:
            return []

        accepted = []
        callbacks = []
        with self._lock:
            combined = self._partial + data
            lines = combined.splitlines(keepends=True)
            if lines and not lines[-1].endswith(("\n", "\r")):
                self._partial = lines.pop()
                if len(self._partial) > self.MAX_PARTIAL_LINE:
                    self._partial = self._partial[-self.MAX_PARTIAL_LINE:]
            else:
                self._partial = ""

            for line in lines:
                event = self._parse_line(line)
                if event is None:
                    continue
                workflow_id = event["workflow_id"]
                sequence = event["sequence"]
                previous = self._workflows.get(workflow_id)
                if previous is not None and sequence <= previous.sequence:
                    continue
                self._workflows[workflow_id] = BootstrapCursor(workflow_id, sequence, event)
                accepted.append(event)
                if self._on_event is not None:
                    callbacks.append(event)

        for event in callbacks:
            self._on_event(event)
        return accepted

    @staticmethod
    def is_terminal(event: dict) -> bool:
        return isinstance(event, dict) and event.get("event") in TERMINAL_EVENTS

    @staticmethod
    def _parse_line(line: str) -> Optional[dict]:
        match = _EVENT_RE.search(line)
        if not match:
            return None
        try:
            event = json.loads(match.group(1))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(event, dict) or event.get("protocol") != PROTOCOL:
            return None
        workflow_id = event.get("workflow_id")
        sequence = event.get("sequence")
        event_name = event.get("event")
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            return None
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            return None
        if event_name not in ALLOWED_EVENTS:
            return None
        if event_name == "stage_started":
            stage = event.get("stage")
            if not isinstance(stage, str) or not stage.strip():
                return None
        return event
