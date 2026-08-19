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
_EVENT_RE = re.compile(r"^\s*=== KACE_BOOTSTRAP_EVENT:\s*(\{[^\r\n]*\})\s*===")


class BootstrapLaunchEchoFilter:
    """Hide Studio's bootstrap launch command when an interactive PTY echoes it."""

    PREFIX = "KACE_STUDIO_LAUNCH=1;"

    def __init__(self):
        self._candidate = ""
        self._dropping = False

    def feed(self, data: str) -> str:
        if not isinstance(data, str) or not data:
            return ""
        visible: list[str] = []
        for char in data:
            if self._dropping:
                if char in "\r\n":
                    self._dropping = False
                    visible.append(char)
                continue
            if self._candidate:
                self._candidate += char
                if self._candidate == self.PREFIX:
                    self._candidate = ""
                    self._dropping = True
                elif self.PREFIX.startswith(self._candidate):
                    continue
                else:
                    visible.append(self._candidate)
                    self._candidate = ""
                continue
            if char == self.PREFIX[0]:
                self._candidate = char
            else:
                visible.append(char)
        return "".join(visible)

    def flush(self) -> str:
        candidate = self._candidate
        self._candidate = ""
        self._dropping = False
        return candidate


class MachineProtocolDisplayFilter:
    """Remove machine-only marker lines from the human SSH transcript.

    Parsers must consume the original stream before this filter is applied.
    The incremental implementation avoids buffering ordinary prompts that do
    not end in a newline, which is required for interactive SSH terminals.
    """

    PREFIXES = (
        "=== KACE_BOOTSTRAP_EVENT:",
        "=== KACE_WORKFLOW_EVENT:",
        "=== KACE_RESULT:",
    )
    AUTHORITATIVE_PREFIXES = (
        "=== STAGE:",
        "=== KACE_BOOTSTRAP_ERROR:",
    )

    def __init__(self):
        self._launch_echo_filter = BootstrapLaunchEchoFilter()
        self._at_line_start = True
        self._candidate = ""
        self._dropping = False
        self._skip_lf = False
        self._authoritative_bootstrap = False

    def enable_authoritative_bootstrap(self) -> None:
        """Hide legacy markers once versioned bootstrap events are confirmed."""
        self._authoritative_bootstrap = True

    def _prefixes(self) -> tuple[str, ...]:
        if self._authoritative_bootstrap:
            return self.PREFIXES + self.AUTHORITATIVE_PREFIXES
        return self.PREFIXES

    def feed(self, data: str) -> str:
        if not isinstance(data, str) or not data:
            return ""
        data = self._launch_echo_filter.feed(data)
        if not data:
            return ""
        prefixes = self._prefixes()
        visible: list[str] = []
        for char in data:
            if self._skip_lf:
                self._skip_lf = False
                if char == "\n":
                    continue
            if self._dropping:
                if char == "\r":
                    self._dropping = False
                    self._at_line_start = True
                    self._skip_lf = True
                elif char == "\n":
                    self._dropping = False
                    self._at_line_start = True
                continue

            if self._candidate:
                self._candidate += char
                if any(prefix.startswith(self._candidate) for prefix in prefixes):
                    continue
                if any(self._candidate.startswith(prefix) for prefix in prefixes):
                    self._candidate = ""
                    self._dropping = True
                    continue
                visible.append(self._candidate)
                self._at_line_start = self._candidate.endswith(("\r", "\n"))
                self._candidate = ""
                continue

            if self._at_line_start and char == "=":
                self._candidate = char
                continue

            visible.append(char)
            self._at_line_start = char in "\r\n"
        return "".join(visible)

    def flush(self) -> str:
        """Release a non-protocol partial candidate when the stream closes."""
        launch_candidate = self._launch_echo_filter.flush()
        if self._dropping:
            self._dropping = False
            self._at_line_start = True
            self._skip_lf = False
            return launch_candidate
        candidate = self._candidate
        self._candidate = ""
        self._at_line_start = not candidate or candidate.endswith(("\r", "\n"))
        self._skip_lf = False
        return launch_candidate + candidate


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
