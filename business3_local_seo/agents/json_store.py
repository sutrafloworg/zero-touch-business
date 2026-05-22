"""
Shared atomic JSON-write helpers.

Why this exists:
    Every writer in this codebase was doing `with open(path, "w") as f: json.dump(...)`.
    If the process is killed mid-write (Docker restart, OOM, ctrl-C, deploy), the file
    is left half-written. The next read raises JSONDecodeError and the safe-load fallback
    returns an EMPTY dict — silently wiping every customer record, every testimonial,
    every pending lead. That is unacceptable for files like customers.json.

    `atomic_write_json` writes to a sibling temp file, fsyncs it, then os.replace()s it
    over the target — which is an atomic operation on POSIX (and atomic-enough on Windows
    for our purposes). A killed process can leave a `.tmp` file behind but never corrupts
    the real file.

`safe_load_json` is a small convenience that returns a default on parse error.

NOTE: This is in-process atomicity only. For cross-process safety (webhook + orchestrator
both writing pending_reports.json) you still want a file lock — that's a separate fix.
"""
import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def atomic_write_json(path: Path, data, *, indent: int = 2) -> None:
    """Write JSON to `path` atomically (write to *.tmp then os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use NamedTemporaryFile in the same directory so os.replace is atomic
    # (replace requires source and destination on the same filesystem).
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, default=str)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # fsync isn't supported on some virtual filesystems — best-effort only
                pass
        os.replace(tmp_path, str(path))
    except Exception:
        # Don't leave the temp file behind on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def safe_load_json(path: Path, default):
    """Read JSON from `path`. Return `default` on missing file or parse error."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default
    except Exception as e:
        logger.warning(f"safe_load_json: unexpected error reading {path}: {e}")
        return default
