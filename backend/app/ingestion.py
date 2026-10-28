"""Bounded source storage, provenance-preserving parsing, and managed ingestion."""

from __future__ import annotations

import asyncio
import hashlib
import re
import zipfile
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from fastapi import UploadFile
from llama_index.core.node_parser import SentenceSplitter

from .config import Settings
from .models import Chunk
from .pipeline import Pipeline
from .repository import Repository


SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}
READ_SIZE = 64 * 1024
MAX_DOCX_EXPANDED_BYTES = 64 * 1024 * 1024
STORAGE_NAME = re.compile(r"[a-f0-9]{32}\.(?:pdf|docx|txt|md)\Z")


class IngestionError(Exception):
    """A safe, actionable error that may be returned to the user."""


class UploadLimitError(IngestionError):
    pass


def validate_name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > 200:
        raise IngestionError("Document names must contain between 1 and 200 characters.")
    if name in {".", ".."} or "/" in name or "\\" in name or not name.isprintable():
        raise IngestionError("Document names cannot contain paths or control characters.")
    return name


def validate_text(text: str, settings: Settings) -> None:
    if not text.strip():
        raise IngestionError("The document does not contain any text.")
    if "\x00" in text:
        raise IngestionError("Text documents cannot contain null characters.")
    if len(text) > settings.max_document_chars:
        raise UploadLimitError("Document text exceeds the configured character limit.")


class SourceStorage:
    def __init__(self, data_dir: Path | str, max_upload_mb: int | float):
        self.directory = Path(data_dir).resolve() / "uploads"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_bytes = int(max_upload_mb * 1024 * 1024)

    def path(self, storage_name: str) -> Path:
        if not STORAGE_NAME.fullmatch(storage_name):
            raise IngestionError("The stored source is unavailable. Delete it and upload it again.")
        return self.directory / storage_name

    def remove(self, storage_name: str) -> None:
        self.path(storage_name).unlink(missing_ok=True)

    async def upload(self, file: UploadFile, suffix: str) -> tuple[str, str]:
        storage_name = uuid4().hex + suffix
        path, digest, size = self.path(storage_name), hashlib.sha256(), 0
        try:
            with path.open("xb") as destination:
                while block := await file.read(READ_SIZE):
                    size += len(block)
                    if size > self.max_bytes:
                        raise UploadLimitError("The upload exceeds the configured file size limit.")
                    digest.update(block)
                    destination.write(block)
            if size == 0:
                raise IngestionError("The uploaded file is empty.")
            return storage_name, digest.hexdigest()
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    def text(self, text: str) -> tuple[str, str]:
        try:
            payload = text.encode("utf-8")
        except UnicodeEncodeError:
            raise IngestionError("Text must contain valid Unicode characters.") from None
        if len(payload) > self.max_bytes:
            raise UploadLimitError("The text exceeds the configured file size limit.")
        storage_name = uuid4().hex + ".txt"
        path = self.path(storage_name)
        try:
            with path.open("xb") as destination:
                destination.write(payload)
            return storage_name, hashlib.sha256(payload).hexdigest()
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def fixture(self, source: Path) -> tuple[str, str]:
        storage_name = uuid4().hex + source.suffix.lower()
        path, digest, size = self.path(storage_name), hashlib.sha256(), 0
        try:
            with source.open("rb") as origin, path.open("xb") as destination:
                while block := origin.read(READ_SIZE):
                    size += len(block)
                    if size > self.max_bytes:
                        raise UploadLimitError("The sample exceeds the configured file size limit.")
                    digest.update(block)
                    destination.write(block)
            if size == 0:
                raise IngestionError("The sample file is empty.")
            return storage_name, digest.hexdigest()
        except BaseException:
            path.unlink(missing_ok=True)
            raise


def _page_has_images(page: Any) -> bool:
    resources = page.get("/Resources")
    if resources is None:
        return False
    pending, seen = [resources], set()
    while pending:
        current = pending.pop().get_object()
        if id(current) in seen:
            continue
        seen.add(id(current))
        objects = current.get("/XObject")
        if objects is None:
            continue
        for reference in objects.get_object().values():
            item = reference.get_object()
            if item.get("/Subtype") == "/Image":
                return True
            if item.get("/Subtype") == "/Form" and item.get("/Resources") is not None:
                pending.append(item.get("/Resources"))
    return False


def _pdf_pages(path: Path, max_chars: int) -> list[tuple[int | None, str]]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    if reader.is_encrypted and not reader.decrypt(""):
        raise IngestionError("Password-protected PDFs are not supported.")
    pages, total = [], 0
    for number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        total += len(text)
        if total > max_chars:
            raise UploadLimitError("Document text exceeds the configured character limit.")
        if not text.strip() and _page_has_images(page):
            raise IngestionError("Scanned PDF pages require OCR, which is not supported.")
        if text.strip():
            pages.append((number, text))
    if not pages:
        raise IngestionError("This PDF has no extractable text. Scanned PDFs require OCR, which is not supported.")
    return pages


def _docx_text(path: Path, max_chars: int) -> str:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    with zipfile.ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > MAX_DOCX_EXPANDED_BYTES:
            raise UploadLimitError("The expanded DOCX exceeds the supported size limit.")
    document, pieces, total = Document(path), [], 0
    for element in document.element.body:
        if element.tag == qn("w:p"):
            text = Paragraph(element, document).text
        elif element.tag == qn("w:tbl"):
            table = Table(element, document)
            text = "\n".join("\t".join(cell.text for cell in row.cells) for row in table.rows)
        else:
            continue
        total += len(text) + (1 if pieces else 0)
        if total > max_chars:
            raise UploadLimitError("Document text exceeds the configured character limit.")
        pieces.append(text)
    return "\n".join(pieces)


def _sentences(text: str) -> list[str]:
    return [piece for piece in re.findall(r".*?(?:[.!?](?=\s|$)|\n|$)", text, flags=re.DOTALL) if piece]


def parse_document(path: Path, document_id: str, name: str, settings: Settings) -> list[Chunk]:
    try:
        if path.is_symlink() or not path.is_file():
            raise IngestionError("The stored source is unavailable. Delete it and upload it again.")
        if path.stat().st_size > settings.max_upload_mb * 1024 * 1024:
            raise UploadLimitError("The upload exceeds the configured file size limit.")
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            pages = _pdf_pages(path, settings.max_document_chars)
        elif suffix == ".docx":
            pages = [(None, _docx_text(path, settings.max_document_chars))]
        elif suffix in {".txt", ".md"}:
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                raise IngestionError("TXT and Markdown files must use UTF-8 encoding.") from None
            pages = [(None, text)]
        else:
            raise IngestionError("Supported document formats are PDF, DOCX, TXT, and MD.")
        # Local tokenization avoids implicit model or NLTK downloads during ingestion.
        splitter = SentenceSplitter(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            tokenizer=lambda text: re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE),
            chunking_tokenizer_fn=_sentences,
        )
        chunks = []
        for page, text in pages:
            validate_text(text, settings)
            for content in splitter.split_text(text):
                content = content.strip()
                if not content:
                    continue
                if len(chunks) >= settings.max_chunks_per_document:
                    raise UploadLimitError("The document exceeds the configured chunk limit.")
                if len(content) > settings.max_context_chars:
                    raise UploadLimitError("A text chunk exceeds the context limit. Split long unbroken text and try again.")
                position = len(chunks)
                chunks.append(Chunk(
                    id=f"{document_id}-{position}",
                    document_id=document_id,
                    document_name=name,
                    text=content,
                    page=page,
                    position=position,
                ))
        if not chunks:
            raise IngestionError("The document does not contain any usable text.")
        return chunks
    except IngestionError:
        raise
    except Exception:
        raise IngestionError("The file could not be parsed. Verify its format and upload it again.") from None


class IngestionWorker:
    def __init__(
        self, settings: Settings, repository: Repository, storage: SourceStorage,
        pipeline: Pipeline, pipeline_lock: asyncio.Lock,
        on_unavailable: Callable[[str], None],
    ):
        self.settings = settings
        self.repository = repository
        self.storage = storage
        self.pipeline = pipeline
        self.pipeline_lock = pipeline_lock
        self.on_unavailable = on_unavailable
        self.wakeup = asyncio.Event()
        self.stopping = False
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._run(), name="document-ingestion")

    def notify(self) -> None:
        self.wakeup.set()

    async def stop(self) -> None:
        self.stopping = True
        self.wakeup.set()
        task = self.task
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise
        finally:
            self.task = None

    async def _run(self) -> None:
        try:
            while not self.stopping:
                self.wakeup.clear()
                claimed = self.repository.claim_next_job()
                if claimed:
                    await self._process(*claimed)
                    continue
                try:
                    await asyncio.wait_for(self.wakeup.wait(), timeout=1)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:
            self.on_unavailable("The ingestion worker is unavailable. Restart the application.")

    async def _process(self, document: dict[str, Any], job: dict[str, Any]) -> None:
        try:
            chunks = await asyncio.to_thread(
                parse_document, self.storage.path(document["storage_name"]),
                document["id"], document["name"], self.settings,
            )
            self.repository.update_job(job["id"], "indexing", 0.45)
            async with self.pipeline_lock:
                counts = await self.pipeline.ingest(document["id"], document["name"], chunks)
                if any(type(counts.get(key)) is not int or counts[key] < 0
                       for key in ("chunks", "entities", "relationships")):
                    raise ValueError("Invalid ingestion counts")
                self.repository.complete_job(job["id"], counts)
        except asyncio.CancelledError:
            await self._fail(document["id"], job["id"],
                             "Ingestion was interrupted during shutdown. Retry this document.")
            raise
        except IngestionError as error:
            await self._fail(document["id"], job["id"], str(error))
        except Exception:
            await self._fail(document["id"], job["id"],
                             "Indexing failed. Check the configured services and retry this document.")

    async def _fail(self, document_id: str, job_id: str, error: str) -> None:
        try:
            async with asyncio.timeout(5):
                async with self.pipeline_lock:
                    await self.pipeline.delete(document_id)
        except Exception:
            # Read routes explicitly exclude failed documents even if cleanup is unavailable.
            pass
        finally:
            self.repository.fail_job(job_id, error)
