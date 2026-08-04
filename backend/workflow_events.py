"""Parse KACE workflow events from an arbitrary SSH byte/chunk stream.

This module is intentionally observational: it never advances KACE, infers a
successful installation, or controls the SSH process.  It only validates and
deduplicates events that KACE itself emitted.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Callable, Dict, Optional


EVENT_PREFIX = "=== KACE_WORKFLOW_EVENT:"
_EVENT_RE = re.compile(r"=== KACE_WORKFLOW_EVENT:\s*(\{[^\r\n]*\})\s*===")


@dataclass(frozen=True)
class WorkflowCursor:
    workflow_id: str
    sequence: int
    event: dict


class KaceWorkflowEventParser:
    """Incrementally parse, group, and order workflow events by workflow_id."""

    MAX_PARTIAL_LINE = 64 * 1024

    def __init__(self, on_event: Optional[Callable[[dict], None]] = None):
        self._on_event = on_event
        self._partial = ""
        self._workflows: Dict[str, WorkflowCursor] = {}
        self._lock = threading.Lock()

    @property
    def workflows(self) -> dict[str, WorkflowCursor]:
        with self._lock:
            return dict(self._workflows)

    def feed(self, data: str) -> list[dict]:
        """Consume a possibly fragmented terminal chunk and return accepted events."""
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
                self._workflows[workflow_id] = WorkflowCursor(workflow_id, sequence, event)
                accepted.append(event)
                if self._on_event is not None:
                    callbacks.append(event)

        # Do not invoke application/UI code while holding the parser lock.
        for event in callbacks:
            self._on_event(event)
        return accepted

    @staticmethod
    def _parse_line(line: str) -> Optional[dict]:
        match = _EVENT_RE.search(line)
        if not match:
            return None
        try:
            event = json.loads(match.group(1))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(event, dict):
            return None
        workflow_id = event.get("workflow_id")
        sequence = event.get("sequence")
        state = event.get("state")
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            return None
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            return None
        if not isinstance(state, str) or not state.strip():
            return None
        return event
