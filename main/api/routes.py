"""HTTP endpoints delegate execution and storage to the existing backend."""

import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from main.api.schemas import TaskInput
from main.api.views import ACTIVE, mapping, public_data, record_view, report_files, run_summary, run_time, task_view
from main.core import run_store
from main.core.model_provider import PROVIDERS


router = APIRouter(prefix="/api/v1")
_PROVIDER_NAMES = {"deepseek": "DeepSeek", "openrouter": "OpenRouter", "siliconflow": "SiliconFlow",
                   "qwen": "Qwen", "custom": "Custom OpenAI-Compatible"}


def fail(status, code, message):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def records(request):
    return run_store.list_runs(root=request.app.state.store_root)


def run_record(request, run_id):
    # load_run takes task_id; never assume it equals the public Core run_id.
    matches = [record for record in records(request)
               if mapping(record.get("metadata")).get("run_id") == run_id]
    if not matches:
        fail(404, "run_not_found", "Run does not exist")
    if len(matches) > 1:
        fail(409, "ambiguous_run_id", "Run ID is associated with multiple tasks")
    metadata = mapping(matches[0].get("metadata"))
    try:
        record = run_store.load_run(metadata.get("task_id") or run_id, root=request.app.state.store_root)
    except ValueError:
        record = None
    if record is None or mapping(record.get("metadata")).get("run_id") != run_id:
        fail(404, "run_not_found", "Run does not exist")
    return record


def stored_task(request, task_id):
    try:
        record = run_store.load_run(task_id, root=request.app.state.store_root)
    except ValueError:
        record = None
    if record is None:
        fail(404, "task_not_found", "Task does not exist")
    metadata = mapping(record.get("metadata"))
    if metadata.get("task_id", task_id) != task_id:
        fail(404, "task_not_found", "Task does not exist")
    return record


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/providers")
def providers():
    return {"items": [{"id": provider, "name": _PROVIDER_NAMES[provider], "default_base_url": url,
                       "requires_base_url": url is None,
                       "credentials_configured": bool(os.getenv(key, "").strip()) if key else False}
                      for provider, (url, key) in PROVIDERS.items()]}


@router.get("/tools")
def tools():
    from main.core.tool_registry import list_tool_options
    return {"items": list_tool_options()}


@router.post("/tasks", status_code=202)
def start_task(config: TaskInput, request: Request):
    if config.temporary_credentials is not None:
        # Browsers sending secrets must be the local Vue UI or same-origin docs.
        # CORS alone only prevents reading a response, not sending a POST.
        origin = request.headers.get("origin")
        local_origins = {"http://127.0.0.1:5173", "http://localhost:5173"}
        own = str(request.base_url).rstrip("/")
        if urlsplit(own).hostname in ("127.0.0.1", "localhost"):
            local_origins.add(own)
        if origin is not None and origin not in local_origins:
            fail(403, "credential_origin_rejected", "Temporary credentials require a trusted local origin")
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            fail(415, "json_required", "Use application/json")
    state = request.app.state
    with state.task_gate:
        if not state.accepting_tasks:
            fail(409, "service_stopping", "Service is shutting down")
        manager = state.task_manager
        current = manager.snapshot()
        if current is not None and current.status in ACTIVE:
            fail(409, "task_conflict", "A task is still running or being cleaned up")
        expected = current.task_id if current else None
        credentials = config.temporary_credentials.to_memory() if config.temporary_credentials else None
        try:
            kwargs = {"temporary_credentials": credentials} if credentials else {}
            task = manager.start(config.to_core(), expected_task_id=expected, **kwargs)
        finally:
            if credentials is not None:
                credentials.clear()
        if task is None or task.task_id == expected:
            fail(409, "task_conflict", "Task state changed; refresh before starting")
    return {"task_id": task.task_id, "status": task.status, "message": "Task accepted"}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request):
    task = request.app.state.task_manager.snapshot()
    if task is not None and task.task_id == task_id:
        return task_view(task)
    record = stored_task(request, task_id)
    return public_data({**run_summary(record), "error": record.get("error"),
                        "config": record.get("config"), "progress": None,
                        "cleanup_error": record.get("cleanup_error")})


@router.post("/tasks/{task_id}/cancel", status_code=202)
def cancel_task(task_id: str, request: Request):
    state = request.app.state
    with state.task_gate:
        task = state.task_manager.snapshot()
        if task is None or task.task_id != task_id:
            stored_task(request, task_id)  # 404 if missing, 409 if already stored.
            fail(409, "task_not_cancellable", "Task has already ended")
        if task.status not in ACTIVE:
            fail(409, "task_not_cancellable", "Task has already ended")
        task = state.task_manager.cancel(task_id, expected_task_id=task_id)
        # A late cancel can race completion or replacement: never claim acceptance.
        if task is None or task.task_id != task_id or task.status not in ("cancelling", "cancelled"):
            fail(409, "task_not_cancellable", "Task state changed before cancellation")
    return {"task_id": task.task_id, "status": task.status,
            "message": "Task cancelled" if task.status == "cancelled" else "Cancellation requested"}


@router.get("/runs")
def list_history(request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                 test_type: Literal["runtime", "correctness"] | None = None):
    items = records(request)
    if test_type:
        items = [item for item in items if str(item.get("test_type", "")).lower() == test_type]
    items.sort(key=run_time, reverse=True)
    return {"items": [run_summary(item) for item in items[offset:offset + limit]],
            "total": len(items), "limit": limit, "offset": offset}


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request):
    return record_view(run_record(request, run_id), request.app.state.store_root)


@router.get("/runs/{run_id}/reports/{filename}")
def report(run_id: str, filename: str, request: Request):
    if filename != Path(filename).name or "/" in filename or "\\" in filename:
        fail(404, "report_not_found", "Report does not exist")
    record = run_record(request, run_id)
    path = report_files(record, request.app.state.store_root).get(filename)
    if path is None:
        fail(404, "report_not_found", "Report does not exist")
    try:
        data = path.read_bytes()
        if path.suffix == ".json":
            content = json.dumps(public_data({"config": record.get("config"),
                                              "report": json.loads(data)})["report"], ensure_ascii=False)
            media_type = "application/json"
        elif path.suffix == ".html":
            content = public_data({"config": record.get("config"), "report": data.decode("utf-8")})["report"]
            media_type = "text/html"
        else:
            content, media_type = data, "image/png"
    except (OSError, ValueError, UnicodeError):
        fail(404, "report_not_found", "Report is missing or unreadable")
    # HTML is inline so its relative PNG/JSON links resolve against this endpoint.
    # Sandbox saved HTML; scripts/cross-origin fetches cannot access the local API.
    return Response(content, media_type=media_type, headers={
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline' https://cdn.jsdelivr.net; base-uri 'none'; form-action 'none'",
    })
