"""Defensive display adapters. No test execution or verdict reinterpretation."""

import json
import math
from pathlib import Path

from main.core.run_store import DEFAULT_RESULTS_DIR, resolve_report


def mapping(value):
    return value if isinstance(value, dict) else {}


def records(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def result_kind(result):
    return str(mapping(result).get("test_type", "")).lower()


def groups(result):
    return records(mapping(result).get("results"))


def case_records(result):
    if result_kind(result) == "correctness":
        return [case for group in groups(result) for case in records(group.get("individual_run_test_results"))]
    return groups(result)


def dashboard(result):
    cases = case_records(result)
    field = "tool_failure" if result_kind(result) == "correctness" else "successful_trigger"
    yes = sum(case.get(field) is True for case in cases)
    no = sum(case.get(field) is False for case in cases)
    duration = mapping(mapping(result).get("metadata")).get("duration_seconds")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
        duration = None
    return {"count": len(cases), "groups": len(groups(result)), "yes": yes, "no": no,
            "unknown": len(cases) - yes - no, "duration": duration}


def verdict(record, kind):
    value = mapping(record).get("tool_failure" if kind == "correctness" else "successful_trigger")
    if value is True:
        return "tool_failures" if kind == "correctness" else "triggered"
    if value is False:
        return "no_failure" if kind == "correctness" else "not_triggered"
    return "unknown"


def matches(record, kind, selected):
    return selected == "all" or verdict(record, kind) == selected


def parse_trace(value):
    if value is None or value == "" or value == [] or value == {}:
        return [], "trace_empty"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError, RecursionError):
            return [], "trace_invalid"
    if isinstance(value, list):
        if not value:
            return [], "trace_empty"
        if all(isinstance(event, dict) for event in value):
            return value, None
    if isinstance(value, dict):
        if "role" in value:
            return [value], None
        return [], "trace_diagnostic" if value else "trace_empty"
    return [], "trace_invalid"


def read_report(artifacts, kind, *, allowed_root=None, task_id=None):
    """Read only the actual artifact returned by a runner; never regenerate it."""
    path = mapping(artifacts).get(f"{kind}_path")
    if not isinstance(path, str) or not path.strip() or path == ".":
        return None, None, "report_missing", None
    try:
        report = Path(path)
        if allowed_root is not None or (task_id is not None and not report.is_absolute()):
            root = Path(DEFAULT_RESULTS_DIR if allowed_root is None else allowed_root).resolve()
            if task_id is None:
                # Compatibility for standalone callers; GUI supplies its record ID.
                parts = report.relative_to(root).parts if report.is_absolute() else report.parts
                if parts and parts[0] == root.name:
                    parts = parts[1:]
                if len(parts) != 2:
                    if not (root / report).exists() and not (root / report).is_symlink():
                        return None, None, "report_missing", None
                    return None, None, "report_outside_store", None
                task_id = parts[0]
            try:
                report = resolve_report(path, kind, task_id=task_id, root=root)
            except ValueError:
                return None, None, "report_outside_store", None
        if report is None or not report.is_file():
            return None, None, "report_missing", None
        return report.name, report.read_bytes(), None, None
    except ValueError:
        return None, None, "report_outside_store", None
    except (OSError, ValueError, RuntimeError) as exc:
        return None, None, "report_unreadable", str(exc)
