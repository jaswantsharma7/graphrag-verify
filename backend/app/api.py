"""Authenticated API routes and document readiness boundaries."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .ingestion import (
    SUPPORTED_SUFFIXES, IngestionError, IngestionWorker, SourceStorage,
    UploadLimitError, validate_name, validate_text,
)
from .models import GraphView, QueryRequest, QueryResult
from .pipeline import Pipeline
from .repository import Repository, RepositoryConflict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TextDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)


@dataclass
class Services:
    settings: Settings
    repository: Repository
    storage: SourceStorage
    pipeline: Pipeline
    lock: asyncio.Lock
    worker: IngestionWorker | None = None
    pipeline_ready: bool = False
    message: str = "Pipeline initialization has not finished."

    def unavailable(self, message: str) -> None:
        self.pipeline_ready = False
        self.message = message


def services(request: Request) -> Services:
    return request.app.state.services


def require_ready(service: Services) -> None:
    if not service.pipeline_ready:
        raise HTTPException(503, service.message)


def ingestion_exception(error: IngestionError) -> HTTPException:
    return HTTPException(413 if isinstance(error, UploadLimitError) else 400, str(error))


def selected_documents(service: Services, requested: list[str]) -> list[str]:
    documents = {document["id"]: document for document in service.repository.list_documents()}
    if not requested:
        return [key for key, document in documents.items() if document["status"] == "ready"]
    ids = list(dict.fromkeys(requested))
    if any(document_id not in documents for document_id in ids):
        raise HTTPException(404, "A document filter refers to a missing document.")
    if any(documents[document_id]["status"] != "ready" for document_id in ids):
        raise HTTPException(409, "Document filters must refer to ready documents.")
    return ids


def register_source(service: Services, name: str, source: tuple[str, str]) -> dict[str, Any]:
    storage_name, content_hash = source
    try:
        document, job, created = service.repository.create_document(name, content_hash, storage_name)
    except Exception:
        service.storage.remove(storage_name)
        raise HTTPException(500, "The document could not be saved. Please try again.") from None
    if not created:
        service.storage.remove(storage_name)
    if service.worker:
        service.worker.notify()
    return {"document": document, "job": job}


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        service = services(request)
        stats = service.repository.counts()
        if service.pipeline_ready:
            try:
                async with service.lock:
                    pipeline_stats = await service.pipeline.stats()
                for key in ("chunks", "entities", "relationships"):
                    stats[key] = int(pipeline_stats[key])
            except Exception:
                service.unavailable("Pipeline health checks failed. Check service connections and restart.")
        return {
            "status": "ok" if service.pipeline_ready else "degraded",
            "mode": service.settings.app_mode,
            "version": request.app.version,
            "pipeline_ready": service.pipeline_ready,
            "message": service.message,
            "stats": stats,
        }

    @router.get("/documents")
    async def documents(request: Request) -> dict[str, Any]:
        return {"documents": services(request).repository.list_documents()}

    @router.post("/documents", status_code=202)
    async def upload_document(
        request: Request, file: UploadFile = File(...), name: str | None = Form(None),
    ) -> dict[str, Any]:
        service = services(request)
        try:
            require_ready(service)
            filename = validate_name(file.filename or "")
            display_name = validate_name(name if name is not None else filename)
            suffix = Path(filename).suffix.lower()
            if suffix not in SUPPORTED_SUFFIXES:
                raise IngestionError("Supported document formats are PDF, DOCX, TXT, and MD.")
            source = await service.storage.upload(file, suffix)
            return register_source(service, display_name, source)
        except IngestionError as error:
            raise ingestion_exception(error) from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(500, "The upload could not be saved. Please try again.") from None
        finally:
            await file.close()

    @router.post("/documents/text", status_code=202)
    async def text_document(request: Request, payload: TextDocumentRequest) -> dict[str, Any]:
        service = services(request)
        require_ready(service)
        try:
            name = validate_name(payload.name)
            validate_text(payload.text, service.settings)
            source = service.storage.text(payload.text)
            return register_source(service, name, source)
        except IngestionError as error:
            raise ingestion_exception(error) from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(500, "The document could not be saved. Please try again.") from None

    @router.get("/jobs/{job_id}")
    async def job(request: Request, job_id: str) -> dict[str, Any]:
        result = services(request).repository.get_job(job_id)
        if result is None:
            raise HTTPException(404, "Job not found.")
        return result

    @router.post("/documents/{document_id}/retry", status_code=202)
    async def retry_document(request: Request, document_id: str) -> dict[str, Any]:
        service = services(request)
        async with service.lock:
            document = service.repository.get_document_record(document_id)
            if document is None:
                raise HTTPException(404, "Document not found.")
            if document["status"] != "failed":
                raise HTTPException(409, "Only failed documents can be retried.")
            require_ready(service)
            if not service.storage.path(document["storage_name"]).is_file():
                raise HTTPException(409, "The source is unavailable. Delete this document and upload it again.")
            try:
                await service.pipeline.delete(document_id)
                updated, new_job = service.repository.retry_document(document_id)
            except RepositoryConflict as error:
                raise HTTPException(409, str(error)) from None
            except Exception:
                raise HTTPException(503, "Retry could not start. Check service connections and try again.") from None
        if service.worker:
            service.worker.notify()
        return {"document": updated, "job": new_job}

    @router.delete("/documents/{document_id}", status_code=204)
    async def delete_document(request: Request, document_id: str) -> Response:
        service = services(request)
        initial = service.repository.get_document_record(document_id)
        if initial is None:
            raise HTTPException(404, "Document not found.")
        if initial["status"] in {"queued", "processing"}:
            raise HTTPException(409, "Wait for active ingestion to finish before deleting.")
        async with service.lock:
            document = service.repository.get_document_record(document_id)
            if document is None:
                raise HTTPException(404, "Document not found.")
            if document["status"] in {"queued", "processing"}:
                raise HTTPException(409, "Wait for active ingestion to finish before deleting.")
            require_ready(service)
            try:
                await service.pipeline.delete(document_id)
            except Exception:
                raise HTTPException(503, "The document could not be removed. Check service connections.") from None
            try:
                service.storage.remove(document["storage_name"])
                service.repository.delete_document(document_id)
            except Exception:
                service.repository.fail_document_removal(document_id)
                raise HTTPException(500, "Document removal did not finish. Try deleting it again.") from None
        return Response(status_code=204)

    @router.post("/demo/seed")
    async def seed_demo(request: Request) -> dict[str, Any]:
        service = services(request)
        if service.settings.app_mode != "demo":
            raise HTTPException(409, "Demo samples are available only in demo mode.")
        require_ready(service)
        directory = PROJECT_ROOT / "samples"
        if not directory.is_dir():
            raise HTTPException(404, "Demo sample files are unavailable.")
        fixtures = sorted(
            (path for path in directory.iterdir()
             if path.is_file() and not path.is_symlink() and path.suffix.lower() in SUPPORTED_SUFFIXES),
            key=lambda path: path.name,
        )
        if not fixtures:
            raise HTTPException(404, "Demo sample files are unavailable.")
        results = []
        try:
            for path in fixtures:
                name = validate_name(path.name)
                source = service.storage.fixture(path)
                results.append(register_source(service, name, source))
        except IngestionError as error:
            raise ingestion_exception(error) from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(500, "Demo samples could not be loaded. Please try again.") from None
        return {
            "documents": [result["document"] for result in results],
            "jobs": [result["job"] for result in results],
        }

    @router.post("/query", response_model=QueryResult)
    async def query(request: Request, payload: QueryRequest) -> QueryResult:
        service, started = services(request), perf_counter()
        require_ready(service)
        if payload.document_ids:
            selected_documents(service, payload.document_ids)
        try:
            async with service.lock:
                require_ready(service)
                ids = selected_documents(service, payload.document_ids)
                if ids:
                    result = await service.pipeline.ask(payload.model_copy(update={"document_ids": ids}))
                else:
                    result = QueryResult(
                        id=uuid4().hex, question=payload.question,
                        answer="No indexed document evidence is available. Add a document and try again.",
                        draft="", status="no_evidence", score=0, claims=[], evidence=[],
                        graph=GraphView(), latency_ms=(perf_counter() - started) * 1000,
                        mode=service.settings.app_mode,
                    )
            service.repository.save_query(result.model_dump(mode="json"))
            return result
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "The question could not be processed. Check service connections and try again.") from None

    @router.get("/queries")
    async def queries(request: Request, limit: int = Query(20, ge=1)) -> dict[str, Any]:
        return {"queries": services(request).repository.list_queries(min(limit, 100))}

    @router.get("/graph", response_model=GraphView)
    async def graph(
        request: Request, limit: int = Query(100, ge=1), document_ids: str | None = None,
    ) -> GraphView:
        service = services(request)
        require_ready(service)
        requested = [] if not document_ids else [value.strip() for value in document_ids.split(",")]
        if len(requested) > 1000 or any(not value or len(value) > 160 for value in requested):
            raise HTTPException(400, "Provide valid document ids for the graph filter.")
        if requested:
            selected_documents(service, requested)
        try:
            async with service.lock:
                require_ready(service)
                ids = selected_documents(service, requested)
                if not ids:
                    return GraphView()
                return await service.pipeline.graph(document_ids=ids, limit=min(limit, 100))
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "The graph is unavailable. Check service connections and try again.") from None

    return router
