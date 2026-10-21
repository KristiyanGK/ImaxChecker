"""JSON state file that remembers which screenings were already announced per channel."""

import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

type State = dict[str, set[str]]


def load(path: Path) -> State:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("Could not read state file %s (%s); starting with empty state", path, exc)
        return {}
    if not isinstance(raw, dict):
        log.warning("State file %s has an unexpected shape; starting with empty state", path)
        return {}
    return {
        str(channel): {str(i) for i in ids} for channel, ids in raw.items() if isinstance(ids, list)
    }


def save(path: Path, state: State) -> None:
    """Atomic write: temp file in the same directory, then `os.replace`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {channel: sorted(ids) for channel, ids in sorted(state.items())}
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(payload, tmp, indent=2)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
