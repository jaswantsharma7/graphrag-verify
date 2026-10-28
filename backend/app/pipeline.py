"""Async graph RAG with independent review and a fail-closed display gate."""

import asyncio
import math
import time
import uuid
from collections import Counter

from .config import Settings
from .graph_store import Neo4jGraphStore, SQLiteGraphStore
from .models import Chunk, Claim, Evaluation, Evidence, Generation, GraphView, QueryRequest, QueryResult
from .providers import DemoProvider, LiveProvider, ProviderError, literal_support, sentences


# Evaluation is fallible; only validated source excerpts reach the safe answer.
def gate_claims(generation: Generation, evaluation: Evaluation, evidence: list[Evidence]) -> list[Claim]:
    evidence_ids = {item.id for item in evidence}
    counts = Counter(claim.text for claim in evaluation.claims)
    reviews = {claim.text: claim for claim in evaluation.claims}
    claims = []
    for draft_claim in generation.claims:
        review = reviews.get(draft_claim.text)
        original_ids = set(draft_claim.citations)
        reason = None
        if not original_ids or not original_ids <= evidence_ids:
            reason = "Draft claim has missing or invalid evidence citations."
        elif review is None or counts[draft_claim.text] != 1:
            reason = "Independent evaluator omitted this claim or returned ambiguous reviews."
        elif not review.citations or not set(review.citations) <= evidence_ids or not set(review.citations) <= original_ids:
            reason = "Independent evaluator returned missing, invalid, or substituted citations."
        elif review.status == "supported" and not literal_support(draft_claim.text, evidence, review.citations):
            reason = "Fail-closed excerpt gate: cited text does not contain this complete source sentence."
        if reason:
            claims.append(Claim(text=draft_claim.text, status="unsupported", citations=draft_claim.citations, reason=reason))
        else:
            claims.append(Claim(
                text=draft_claim.text, status=review.status, citations=list(dict.fromkeys(review.citations)),
                reason=review.reason or "Independent evidence review; not a guarantee of real-world truth.",
            ))
    return claims


class Pipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = SQLiteGraphStore(settings) if settings.app_mode == "demo" else Neo4jGraphStore(settings)
        self.provider = DemoProvider(settings) if settings.app_mode == "demo" else LiveProvider(settings)
        self.ready = False
        self.state_lock = asyncio.Lock()

    async def initialize(self) -> None:
        if self.ready:
            return
        if self.settings.app_mode == "live":
            self.settings.validate_live()
        try:
            async with asyncio.timeout(self.settings.request_timeout_seconds * 4):
                await self.store.initialize()
        except Exception:
            await self.store.close()
            message = "Live graph initialization failed. Check Neo4j connectivity, permissions, and vector index configuration." if self.settings.app_mode == "live" else "Demo graph database could not be initialized. Check DATA_DIR access."
            raise RuntimeError(message) from None
        self.ready = True

    def _require_ready(self) -> None:
        if not self.ready:
            raise RuntimeError("Pipeline is not ready. Check configuration and initialize it first.")

    async def ingest(self, document_id: str, name: str, chunks: list[Chunk]) -> dict:
        self._require_ready()
        if not document_id.strip() or len(document_id) > 160 or not name.strip() or len(name) > 255:
            raise ValueError("Document id and name must be valid non-empty values")
        if not chunks or len(chunks) > self.settings.max_chunks_per_document:
            raise ValueError("Document must contain chunks within MAX_CHUNKS_PER_DOCUMENT")
        if len({chunk.id for chunk in chunks}) != len(chunks):
            raise ValueError("Chunk ids must be unique within a document")
        if any(chunk.document_id != document_id or not chunk.text.strip() for chunk in chunks):
            raise ValueError("Chunks must belong to this document and contain text")
        if any(len(chunk.text) > self.settings.max_context_chars for chunk in chunks):
            raise ValueError("Individual chunks must fit MAX_CONTEXT_CHARS")
        if sum(len(chunk.text) for chunk in chunks) > self.settings.max_document_chars * 2:
            raise ValueError("Chunked document exceeds the configured document size")
        embeddings = await self.provider.embed([chunk.text for chunk in chunks])
        if len(embeddings) != len(chunks) or any(
            len(vector) != self.settings.embedding_dimensions or any(not math.isfinite(value) for value in vector)
            for vector in embeddings
        ):
            raise ProviderError("Embedding provider returned invalid vectors")
        persisted = [chunk.model_copy(update={"document_name": name, "embedding": vector}) for chunk, vector in zip(chunks, embeddings, strict=True)]
        extractions = []
        for offset in range(0, len(chunks), self.settings.provider_concurrency):
            batch = chunks[offset:offset + self.settings.provider_concurrency]
            extractions.extend(await asyncio.gather(*(self.provider.extract(chunk) for chunk in batch)))
        async with self.state_lock:
            try:
                return await self.store.replace_document(document_id, name, persisted, extractions)
            except Exception:
                raise RuntimeError("Atomic document replacement failed; the previous graph was preserved.") from None

    @staticmethod
    def _context(candidates: list[Evidence], limit: int, char_limit: int, document_ids: list[str] | None) -> list[Evidence]:
        merged = {}
        selected_documents = set(document_ids) if document_ids is not None else None
        for item in candidates:
            if selected_documents is not None and item.document_id not in selected_documents:
                continue
            prior = merged.get(item.id)
            if prior is None or item.score > prior.score:
                merged[item.id] = item
        result, remaining = [], char_limit
        for item in sorted(merged.values(), key=lambda entry: (-entry.score, entry.id)):
            if remaining < 4 or len(result) >= limit:
                break
            text = item.text
            if len(text) > remaining:
                complete, size = [], 0
                for sentence in sentences(text):
                    if size + len(sentence) + 1 > remaining:
                        break
                    complete.append(sentence)
                    size += len(sentence) + 1
                text = "\n".join(complete)
            if text.strip():
                result.append(item.model_copy(update={"text": text}))
                remaining -= len(text)
        return result

    async def ask(self, request: QueryRequest) -> QueryResult:
        self._require_ready()
        started = time.perf_counter()
        scope = request.document_ids or None
        async with self.state_lock:
            try:
                empty = (await self.store.stats())["chunks"] == 0
            except Exception:
                raise RuntimeError("Graph retrieval failed. Check graph storage availability and configuration.") from None
        if empty:
            return QueryResult(
                id=uuid.uuid4().hex, question=request.question,
                answer="No relevant evidence was found in the selected documents.", draft="", status="no_evidence",
                score=0, claims=[], evidence=[], graph=GraphView(),
                latency_ms=(time.perf_counter() - started) * 1000, mode=self.settings.app_mode,
            )
        embeddings = await self.provider.embed([request.question])
        async with self.state_lock:
            try:
                vectors = await self.store.vector_search(embeddings[0], scope, request.top_k)
                lexical = await self.store.lexical_search(request.question, scope, request.top_k)
                graph_evidence = await self.store.graph_search(
                    request.question, [item.id for item in vectors[:2]], scope, request.hops, request.top_k * 4,
                )
                evidence = self._context(vectors + lexical + graph_evidence, request.top_k * 4, self.settings.max_context_chars, scope)
                graph = await self.store.graph(scope, self.settings.max_graph_nodes, [item.id for item in evidence])
            except Exception:
                raise RuntimeError("Graph retrieval failed. Check graph storage availability and configuration.") from None
        if not evidence:
            answer, draft, claims, status, score = "No relevant evidence was found in the selected documents.", "", [], "no_evidence", 0.0
        else:
            generation = await self.provider.generate(request.question, evidence)
            try:
                evaluation = await self.provider.evaluate(request.question, generation, evidence)
            except ProviderError:
                evaluation = Evaluation(claims=[])
            claims = gate_claims(generation, evaluation, evidence)
            supported = [claim for claim in claims if claim.status == "supported"]
            score = len(supported) / len(claims) if claims else 0.0
            status = "grounded" if supported and len(supported) == len(claims) else "partial" if supported else "ungrounded"
            if supported:
                label = "Extractive demo answer (literal source excerpts)" if self.settings.app_mode == "demo" else "Source-supported excerpts (model-reviewed; not a truth guarantee)"
                answer = label + ":\n" + "\n".join(f"{claim.text} [{', '.join(claim.citations)}]" for claim in supported)
            else:
                answer = "The retrieved evidence does not support an answer to this question."
            draft = generation.answer
        return QueryResult(
            id=uuid.uuid4().hex, question=request.question, answer=answer, draft=draft, status=status,
            score=score, claims=claims, evidence=evidence, graph=graph,
            latency_ms=(time.perf_counter() - started) * 1000, mode=self.settings.app_mode,
        )

    async def delete(self, document_id: str) -> None:
        self._require_ready()
        async with self.state_lock:
            try:
                await self.store.delete(document_id)
            except Exception:
                raise RuntimeError("Document deletion failed; storage transaction was rolled back.") from None

    async def graph(self, document_ids: list[str] | None = None, limit: int = 100) -> GraphView:
        self._require_ready()
        if limit < 1:
            raise ValueError("Graph limit must be positive")
        limit = min(limit, self.settings.max_graph_nodes)
        async with self.state_lock:
            try:
                return await self.store.graph(document_ids or None, limit)
            except Exception:
                raise RuntimeError("Graph view could not be retrieved") from None

    async def stats(self) -> dict:
        self._require_ready()
        async with self.state_lock:
            try:
                return await self.store.stats()
            except Exception:
                raise RuntimeError("Graph statistics could not be retrieved") from None

    async def close(self) -> None:
        async with self.state_lock:
            self.ready = False
            try:
                await self.store.close()
            finally:
                await self.provider.close()
