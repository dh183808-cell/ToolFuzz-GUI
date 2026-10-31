from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4


@dataclass(frozen=True)
class RunContext:
    run_id: str
    started_at: str
    started_perf: float


def start_run():
    started = datetime.now(timezone.utc)
    run_id = (
        f"{started.strftime('%Y%m%dT%H%M%SZ')}_"
        f"{uuid4().hex[:8]}"
    )
    return RunContext(
        run_id=run_id,
        started_at=started.isoformat(timespec="seconds"),
        started_perf=perf_counter(),
    )


def build_run_result(
    *,
    context,
    status,
    test_type,
    tool,
    model,
    config,
    summary,
    results,
    json_path,
    html_path,
):
    finished = datetime.now(timezone.utc)

    return {
        "status": status,
        "test_type": test_type,
        "tool": tool,
        "model": model,
        "config": config,
        "summary": summary,
        "results": results,
        "artifacts": {
            "json_path": str(json_path),
            "html_path": str(html_path),
        },
        "metadata": {
            "run_id": context.run_id,
            "started_at": context.started_at,
            "finished_at": finished.isoformat(timespec="seconds"),
            "duration_seconds": round(
                perf_counter() - context.started_perf,
                3,
            ),
        },
    }
