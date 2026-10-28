"""FastAPI lifecycle, request boundaries, and optional frontend delivery."""

from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .api import PROJECT_ROOT, Services, create_router
from .config import Settings
from .ingestion import IngestionWorker, SourceStorage
from .pipeline import Pipeline
from .repository import Repository


VERSION = "0.1.0"


class APIKeyMiddleware:
    def __init__(self, app: ASGIApp, api_key: str | None):
        self.app = app
        self.expected = api_key.encode("utf-8") if api_key else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] == "http" and self.expected is not None and (
            path == "/api" or path.startswith("/api/")
        ):
            values = [value for name, value in scope.get("headers", []) if name.lower() == b"x-api-key"]
            supplied = values[0] if len(values) == 1 else b""
            if not secrets.compare_digest(supplied, self.expected):
                response = JSONResponse(
                    {"detail": "A valid X-API-Key header is required."}, status_code=401,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class BodyLimitExceeded(MultiPartException):
    def __init__(self):
        super().__init__("Request body size limit exceeded.")


class SourceBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, upload_bytes: int, text_chars: int):
        self.app = app
        self.upload_limit = upload_bytes + 64 * 1024
        self.text_limit = min(upload_bytes * 6, text_chars * 12) + 8192

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "").rstrip("/")
        limit = {"/api/documents": self.upload_limit, "/api/documents/text": self.text_limit}.get(path)
        if limit is None:
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    oversized = int(value) > limit
                except ValueError:
                    oversized = False
                if oversized:
                    await self._reject(scope, receive, send)
                    return
        size, exceeded = 0, False

        async def limited_receive() -> Message:
            nonlocal size, exceeded
            if exceeded:
                raise BodyLimitExceeded()
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > limit:
                    exceeded = True
                    raise BodyLimitExceeded()
            return message

        async def limited_send(message: Message) -> None:
            if not exceeded:
                await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse(
            {"detail": "The request exceeds the configured upload or text size limit."}, status_code=413,
        )(scope, receive, send)


def create_app(
    settings: Settings | None = None, pipeline: Pipeline | None = None,
    frontend_dist: Path | None = None,
) -> FastAPI:
    settings = settings if settings is not None else Settings()
    dist = (frontend_dist if frontend_dist is not None else PROJECT_ROOT / "frontend" / "dist").resolve()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        repository = Repository(settings.data_dir)
        storage = SourceStorage(settings.data_dir, settings.max_upload_mb)
        current_pipeline = pipeline if pipeline is not None else Pipeline(settings)
        service = Services(settings, repository, storage, current_pipeline, asyncio.Lock())
        application.state.services = service
        interrupted = repository.recover_interrupted_jobs()
        try:
            await current_pipeline.initialize()
            for document_id in interrupted:
                await current_pipeline.delete(document_id)
            service.pipeline_ready = True
            service.message = (
                "Demo mode uses deterministic extractive retrieval; no live models are called."
                if settings.app_mode == "demo" else "Live pipeline is ready."
            )
        except Exception:
            service.unavailable(
                "Live pipeline is unavailable. Configure Neo4j and model providers, then restart."
                if settings.app_mode == "live"
                else "Demo pipeline is unavailable. Check the data directory and restart."
            )
        # One Uvicorn worker is supported; pipeline state and exclusion locks are process-local.
        if service.pipeline_ready:
            service.worker = IngestionWorker(
                settings, repository, storage, current_pipeline, service.lock, service.unavailable,
            )
            service.worker.start()
        try:
            yield
        finally:
            try:
                if service.worker:
                    await service.worker.stop()
            finally:
                try:
                    async with asyncio.timeout(5):
                        async with service.lock:
                            await current_pipeline.close()
                except Exception:
                    pass

    application = FastAPI(title="GraphRAG Verify", version=VERSION, lifespan=lifespan)
    application.add_middleware(
        SourceBodyLimitMiddleware,
        upload_bytes=settings.max_upload_mb * 1024 * 1024,
        text_chars=settings.max_document_chars,
    )
    application.add_middleware(APIKeyMiddleware, api_key=settings.api_key)
    origins = [value.strip() for value in settings.cors_origins.split(",") if value.strip()]
    application.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        detail = error.detail if isinstance(error.detail, str) else "The request could not be completed."
        return JSONResponse({"detail": detail}, status_code=error.status_code, headers=error.headers)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": "Invalid request fields or parameter values."}, status_code=422)

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"detail": "The request could not be completed. Please try again."}, status_code=500)

    @application.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    application.include_router(create_router())

    @application.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def frontend(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/") or "\\" in full_path or ":" in full_path:
            raise HTTPException(404, "Not found.")
        if any(ord(character) < 32 or ord(character) == 127 for character in full_path):
            raise HTTPException(404, "Not found.")
        if any(part.startswith(".") for part in Path(full_path).parts):
            raise HTTPException(404, "Not found.")
        try:
            candidate = (dist / full_path).resolve()
        except (OSError, ValueError):
            raise HTTPException(404, "Not found.") from None
        if not candidate.is_relative_to(dist) or candidate.is_relative_to(settings.data_dir):
            raise HTTPException(404, "Not found.")
        if candidate.is_file():
            return FileResponse(candidate)
        index = (dist / "index.html").resolve()
        if index.is_file() and index.is_relative_to(dist) and not index.is_relative_to(settings.data_dir):
            if Path(full_path).suffix:
                raise HTTPException(404, "Not found.")
            return FileResponse(index)
        raise HTTPException(404, "Frontend build is unavailable.")

    return application


app = create_app()
