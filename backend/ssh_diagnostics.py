"""Opt-in, bounded SSH lifecycle evidence without terminal data or credentials."""

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import threading
import time
import traceback

_lock = threading.Lock()
_handler = None
_path = None


def record(event, *, session, phase="", error=None, active=None):
    """Diagnostics must never change connection behavior, even on storage failure."""
    global _handler, _path
    destination = os.environ.get("KACE_STUDIO_SSH_DIAGNOSTICS")
    if not destination:
        return
    try:
        item = dict(event=event, session=session, phase=phase, active=active,
                    unix_ns=time.time_ns(), monotonic_ns=time.monotonic_ns(),
                    pid=os.getpid())
        if error is not None:
            # Exception messages and frame locals may contain commands or secrets.
            # Preserve type, OS error number and code locations only.
            item["error_type"] = type(error).__name__
            number = getattr(error, "errno", None)
            item["errno"] = number if isinstance(number, int) else None
            item["frames"] = [dict(file=Path(f.filename).name, line=f.lineno, function=f.name)
                              for f in traceback.extract_tb(error.__traceback__)]
        with _lock:
            if _path != destination:
                if _handler is not None:
                    _handler.close()
                path = Path(destination)
                if not path.is_absolute():
                    return
                path.parent.mkdir(parents=True, exist_ok=True)
                _handler = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024,
                                              backupCount=3, encoding="utf-8")
                _path = destination
            _handler.emit(logging.LogRecord("ssh_diagnostics", logging.INFO, "", 0,
                                           json.dumps(item), (), None))
    except Exception:
        pass
