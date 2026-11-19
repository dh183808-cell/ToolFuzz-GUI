"""Small read-only HTTP adapters for RunStore records and their artifacts."""

from datetime import datetime, timezone
import re
from urllib.parse import quote

from main.core import run_store


ACTIVE = ("running", "cancelling")
# Hide local paths in diagnostic strings as well as structured path fields.
_LOCAL_PATH = re.compile(r"(?<![\w:/.<])/(?!/)[^\s/\"'<>;,)}\]]+/[^\s\"'<>;,)}\]]*|[A-Za-z]:[\\/][^\s\"'<>;,)}\]]+")


def mapping(value):
    return value if isinstance(value, dict) else {}


def public_data(value):
    """Credential-safe copy without server filesystem paths."""
    value = run_store.redact_credentials(value)

    def clean(item):
        if isinstance(item, dict):
            return {key: None if str(key).endswith("_path") or key == "path" else clean(val)
                    for key, val in item.items()}
        if isinstance(item, list):
            return [clean(val) for val in item]
        if isinstance(item, str):
            return _LOCAL_PATH.sub("[LOCAL_PATH]", item)
        return item

    return clean(value)


def run_time(record):
    value = mapping(record.get("metadata")).get("started_at")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return float("-inf")


def report_files(record, root):
    """Use the shared task-scoped resolver for live and relocated records."""
    return run_store.declared_reports(record, root=root)


def record_view(record, root):
    safe = public_data(record)
    run_id = mapping(record.get("metadata")).get("run_id")
    prefix = f"/api/v1/runs/{quote(str(run_id), safe='')}/reports/"
    files = report_files(record, root)
    artifacts = dict(mapping(safe.get("artifacts")))
    for kind in ("json", "html"):
        matches = [name for name, path in files.items() if path.suffix == f".{kind}"]
        artifacts[f"{kind}_path"] = prefix + quote(matches[0], safe="") if matches else None
    safe["artifacts"] = artifacts
    safe["reports"] = {name: prefix + quote(name, safe="") for name in files}
    return safe


def run_summary(record):
    safe = public_data({
        "config": record.get("config"),
        "task_id": mapping(record.get("metadata")).get("task_id") or mapping(record.get("metadata")).get("run_id"),
        "run_id": mapping(record.get("metadata")).get("run_id"),
        "status": record.get("status"), "run_type": record.get("test_type"),
        "tool": record.get("tool"), "model": record.get("model"),
        "started_at": mapping(record.get("metadata")).get("started_at"),
        "summary": mapping(record.get("summary")),
        "persistence": record.get("persistence", {"status": "stored"}),
    })
    safe.pop("config", None)
    return safe


def task_view(task):
    return public_data({
        "task_id": task.task_id, "run_id": task.core_run_id,
        "status": task.status, "run_type": task.config.test_type.lower(),
        "started_at": task.started_at, "duration_seconds": task.duration_seconds,
        "summary": mapping(mapping(task.result).get("summary")), "progress": None,
        # Config supplies custom key-variable names to the existing redactor.
        "config": {"model_roles": task.config.model_roles.summary() if task.config.model_roles else None},
        "error": task.error, "cleanup_error": task.cleanup_error,
        "persistence": {"status": task.persistence_status, "error": task.persistence_error},
    })
