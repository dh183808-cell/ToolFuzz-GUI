"""Atomic, credential-safe RunResult storage, separate from historical results."""

import json
import os
from pathlib import Path
import re
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "experiment_runs"
HISTORICAL_RESULTS_DIR = PROJECT_ROOT / "main" / "results"
_SECRET_KEY = re.compile(
    r"api.?key|token$|secret|password|authorization|credential|private.?key|cookie",
    re.IGNORECASE,
)
_SECRET_TEXT = re.compile(
    r"(?i)(bearer\s+)[\w.~+/=-]+|\bsk-[\w-]+|"
    r"((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)"
    r"[\"']?\s*[:=]\s*[\"']?)([^\s\"',;}]+)"
)


def redact_credentials(value):
    """Copy JSON data, including serialized traces, without credentials."""
    secrets = [
        secret for key, secret in os.environ.items()
        if _SECRET_KEY.search(key) and len(secret) >= 8
    ]

    def scrub(item):
        if isinstance(item, dict):
            return {
                key: "[REDACTED]" if _SECRET_KEY.search(str(key)) else scrub(val)
                for key, val in item.items()
            }
        if isinstance(item, (list, tuple)):
            return [scrub(val) for val in item]
        if isinstance(item, str):
            # Traces often contain a second JSON document inside a string.
            try:
                nested = json.loads(item)
            except (ValueError, RecursionError):
                nested = None
            if isinstance(nested, (dict, list)):
                item = json.dumps(scrub(nested), ensure_ascii=False)
            for secret in sorted(secrets, key=len, reverse=True):
                item = item.replace(secret, "[REDACTED]")
            return _SECRET_TEXT.sub(
                lambda match: (match.group(1) or match.group(2) or "") + "[REDACTED]",
                item,
            )
        return item

    return scrub(value)


def checked_directory(directory):
    """Reject writes into historical results, including symlink aliases."""
    directory = Path(directory).resolve()
    if directory == HISTORICAL_RESULTS_DIR or HISTORICAL_RESULTS_DIR in directory.parents:
        raise ValueError("Historical main/results is read-only")
    return directory


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", value):
        raise ValueError("Invalid task/run identifier")
    if value in (".", ".."):
        raise ValueError("Invalid task/run identifier")
    return value


def task_directory(task_id, *, root=None):
    root = DEFAULT_RESULTS_DIR if root is None else Path(root)
    directory = checked_directory(root / _identifier(task_id))
    if directory.parent != root.resolve():
        raise ValueError("Task directory must stay inside the store")
    return directory


def report_path(directory, name):
    directory = checked_directory(directory)
    path = directory / _identifier(name)
    # Also reject report symlinks to any location outside the task directory.
    for suffix in (".json", ".html"):
        if Path(str(path) + suffix).resolve().parent != directory:
            raise ValueError("Report must stay inside the task directory")
    return path


def save_run(result, *, task_id=None, root=None, directory=None):
    """Save a complete RunResult; return its path, raising on storage failure.

    ``root`` is a store root. ``directory`` supports the runners' existing
    explicit results_dir API, where the caller supplies the final report folder.
    """
    record = redact_credentials(result)
    run_id = _identifier(record["metadata"]["run_id"])
    task_id = _identifier(task_id or record["metadata"].get("task_id") or run_id)
    record["metadata"]["task_id"] = task_id
    directory = task_directory(task_id, root=root) if directory is None else checked_directory(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "run_result.json"
    fd, temporary = tempfile.mkstemp(prefix=".run_result-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def load_run(task_id, *, root=None):
    """Return a valid record or None for a missing/unreadable/corrupt file."""
    path = task_directory(task_id, root=root) / "run_result.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or not isinstance(record.get("metadata"), dict):
            return None
        if not record["metadata"].get("run_id"):
            return None
        return record
    except (OSError, ValueError, RecursionError):
        return None


def list_runs(*, root=None):
    """List valid records, skipping damaged siblings without rewriting them."""
    root = Path(DEFAULT_RESULTS_DIR if root is None else root)
    records = []
    for directory in sorted(root.glob("*")):
        try:
            record = load_run(directory.name, root=root)
        except ValueError:
            continue
        if record is not None:
            records.append(record)
    return records


def persist_run(result, *, task_id=None, root=None, directory=None):
    """Return a safe result with storage status independent of execution status."""
    result = redact_credentials(result)
    result["metadata"]["task_id"] = task_id or result["metadata"].get("task_id") or result["metadata"]["run_id"]
    try:
        path = save_run(result, task_id=task_id, root=root, directory=directory)
    except Exception as exc:
        result["persistence"] = {
            "status": "failed",
            "error": redact_credentials(f"{type(exc).__name__}: {exc}"),
        }
    else:
        result["persistence"] = {"status": "saved", "path": str(path)}
    return result
