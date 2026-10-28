"""Where a running import has got to, for the progress bar on the upload page.

Kept in memory, keyed by (firm, id the page chose): the API runs as one process, and a progress
reading is worthless after the import ends, so nothing is stored. Entries expire on their own.
"""

from __future__ import annotations

import threading
import time

_TTL = 600.0  # seconds an entry is kept after its last update
_lock = threading.Lock()
_state: dict[tuple[str, str], tuple[float, dict[str, object]]] = {}


def report(tenant_id: str, pid: str | None, stage: str, done: int, total: int) -> None:
    """stage: reading (pages) | saving (products). No-op without an id."""
    if not pid:
        return
    now = time.monotonic()
    with _lock:
        for k in [k for k, (t, _) in _state.items() if now - t > _TTL]:
            del _state[k]
        _state[(tenant_id, pid)] = (now, {"stage": stage, "done": done, "total": total})


def read(tenant_id: str, pid: str) -> dict[str, object] | None:
    with _lock:
        hit = _state.get((tenant_id, pid))
    return hit[1] if hit else None


def clear(tenant_id: str, pid: str | None) -> None:
    if pid:
        with _lock:
            _state.pop((tenant_id, pid), None)
