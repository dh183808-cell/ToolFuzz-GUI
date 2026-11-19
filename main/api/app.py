"""Single-process local FastAPI application; no model work at import time."""

import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from main.api.routes import router
from main.api.views import ACTIVE
from main.core.run_store import DEFAULT_RESULTS_DIR


DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def allowed_origins():
    origins = list(DEFAULT_ORIGINS)
    for value in os.getenv("TOOLFUZZ_CORS_ORIGINS", "").split(","):
        value = value.strip()
        if not value:
            continue
        parsed = urlsplit(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or "*" in value
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise ValueError("TOOLFUZZ_CORS_ORIGINS must contain explicit HTTP(S) origins")
        origins.append(value.rstrip("/"))
    return origins


def create_app(*, manager_factory=None, store_root=None, shutdown_timeout=20):
    """Inject an isolated manager/store in tests; the default uses the real Worker."""
    root = Path(DEFAULT_RESULTS_DIR if store_root is None else store_root).resolve()

    @asynccontextmanager
    async def lifespan(app):
        # Only one Uvicorn worker is supported by this in-process manager.
        if int(os.getenv("WEB_CONCURRENCY", "1")) != 1:
            raise RuntimeError("ToolFuzz requires a single Uvicorn worker")
        if manager_factory is None:
            from GUI.services.task_manager import TaskManager
            manager = TaskManager(store_root=root)
        else:
            manager = manager_factory()
        app.state.task_manager = manager
        app.state.store_root = root
        app.state.task_gate = Lock()
        app.state.accepting_tasks = True
        try:
            yield
        finally:
            with app.state.task_gate:
                app.state.accepting_tasks = False
                task = manager.snapshot()
                if task is not None and task.status in ACTIVE:
                    manager.cancel(task.task_id, expected_task_id=task.task_id)
            deadline = monotonic() + shutdown_timeout
            while True:
                task = manager.snapshot()
                if task is None or task.status not in ACTIVE:
                    break
                if monotonic() >= deadline:
                    # Keep the manager/ownership reference and report failure.
                    # Never pretend resources were reaped or clear an active slot.
                    raise RuntimeError("Task cleanup was not confirmed during API shutdown")
                await asyncio.sleep(.05)

    app = FastAPI(title="ToolFuzz API", version="1.0", lifespan=lifespan, docs_url=None)
    app.mount("/docs-assets/swagger-ui", StaticFiles(
        directory=Path(__file__).resolve().parent / "static" / "swagger-ui",
    ), name="swagger-ui-assets")

    @app.get("/docs", include_in_schema=False)
    async def swagger_docs(request: Request):
        root_path = request.scope.get("root_path", "").rstrip("/")
        assets = root_path + "/docs-assets/swagger-ui"
        return get_swagger_ui_html(
            openapi_url=root_path + app.openapi_url,
            title=app.title + " - Swagger UI",
            oauth2_redirect_url=root_path + app.swagger_ui_oauth2_redirect_url,
            swagger_js_url=assets + "/swagger-ui-bundle.js",
            swagger_css_url=assets + "/swagger-ui.css",
            swagger_favicon_url=assets + "/favicon-32x32.png",
            swagger_ui_parameters={"validatorUrl": None},
        )

    @app.get(app.swagger_ui_oauth2_redirect_url, include_in_schema=False)
    async def swagger_oauth_redirect():
        return get_swagger_ui_oauth2_redirect_html()

    app.add_middleware(CORSMiddleware, allow_origins=allowed_origins(),
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"],
                       allow_credentials=False)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {
            "code": "http_error", "message": "Request could not be handled",
        }
        return JSONResponse({"error": detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # Pydantic error inputs may contain a submitted key or credential URL.
        return JSONResponse({"error": {"code": "validation_error",
                                       "message": "Invalid or unsupported request parameters"}}, status_code=422)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        return JSONResponse({"error": {"code": "internal_error",
                                       "message": "Internal service error"}}, status_code=500)

    @app.middleware("http")
    async def safe_internal_errors(request, call_next):
        try:
            return await call_next(request)
        except Exception:
            # Starlette's outer error middleware otherwise re-raises the original
            # SDK exception to the server logger even after an error response.
            return JSONResponse({"error": {"code": "internal_error",
                                           "message": "Internal service error"}}, status_code=500)

    app.include_router(router)
    return app


app = create_app()
