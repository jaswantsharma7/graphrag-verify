"""Atomic graph persistence, scoped retrieval, and chunk-level provenance."""

import asyncio
import heapq
import json
import sqlite3
import threading
from collections.abc import Callable

from .config import Settings
from .models import Chunk, ChunkExtraction, Evidence, GraphEdge, GraphNode, GraphView, RELATION_TYPES
from .providers import cosine, entity_id, relationship_id, validate_extraction, words


# Storage-neutral facts contain only validated explicit assertions.
def _facts(chunks: list[Chunk], extractions: list[ChunkExtraction]) -> tuple[list[dict], list[dict], list[dict]]:
    entities, mentions, edges = {}, [], {}
    for chunk, raw in zip(chunks, extractions, strict=True):
        extraction = validate_extraction(raw, chunk.text)
        for entity in extraction.entities:
            identifier = entity_id(entity.name)
            entities.setdefault(identifier, {"id": identifier, "label": entity.name, "kind": entity.kind})
            mentions.append({"entity_id": identifier, "chunk_id": chunk.id})
        for relation in extraction.relationships:
            source, target = entity_id(relation.source), entity_id(relation.target)
            identifier = relationship_id(source, relation.kind, target)
            edge = edges.setdefault(identifier, {
                "id": identifier, "source": source, "target": target,
                "kind": relation.kind, "evidence_ids": [],
            })
            if chunk.id not in edge["evidence_ids"]:
                edge["evidence_ids"].append(chunk.id)
    return list(entities.values()), mentions, list(edges.values())


def _evidence(row: dict, score: float, retrieval: str, entities: list[str] | None = None) -> Evidence:
    return Evidence(
        id=row["id"], document_id=row["document_id"], document_name=row["document_name"],
        text=row["text"], page=row.get("page"), score=max(0.0, min(1.0, score)),
        retrieval=retrieval, entity_ids=entities or [],
    )


def _view(nodes: list[dict], edge_rows: list[dict], limit: int, max_edges: int) -> GraphView:
    selected = {node["id"]: GraphNode(**node) for node in nodes[:limit]}
    grouped = {}
    for row in edge_rows:
        if row["source"] not in selected or row["target"] not in selected:
            continue
        edge = grouped.setdefault(row["id"], GraphEdge(
            id=row["id"], source=row["source"], target=row["target"], label=row["kind"], evidence_ids=[],
        ))
        for identifier in row["evidence_ids"]:
            if identifier not in edge.evidence_ids:
                edge.evidence_ids.append(identifier)
    return GraphView(nodes=list(selected.values()), edges=list(grouped.values())[:max_edges])


# Demo storage uses a durable SQLite transaction for each document replacement.
class SQLiteGraphStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.connection: sqlite3.Connection | None = None
        self.lock = threading.RLock()

    async def _run(self, function: Callable, *args):
        def locked():
            with self.lock:
                return function(*args)
        return await asyncio.to_thread(locked)

    @property
    def db(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("Graph store is not initialized")
        return self.connection

    async def initialize(self) -> None:
        def create():
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.settings.data_dir / "graph.sqlite3", check_same_thread=False, isolation_level=None, timeout=10)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS grv_documents(id TEXT PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS grv_chunks(
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES grv_documents(id) ON DELETE CASCADE,
                    document_name TEXT NOT NULL, text TEXT NOT NULL, page INTEGER, position INTEGER NOT NULL,
                    embedding TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS grv_chunks_document ON grv_chunks(document_id);
                CREATE TABLE IF NOT EXISTS grv_entities(id TEXT PRIMARY KEY, label TEXT NOT NULL, kind TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS grv_mentions(
                    entity_id TEXT REFERENCES grv_entities(id) ON DELETE CASCADE,
                    chunk_id TEXT REFERENCES grv_chunks(id) ON DELETE CASCADE,
                    PRIMARY KEY(entity_id,chunk_id)
                );
                CREATE INDEX IF NOT EXISTS grv_mentions_chunk ON grv_mentions(chunk_id);
                CREATE TABLE IF NOT EXISTS grv_edges(
                    id TEXT PRIMARY KEY, source TEXT REFERENCES grv_entities(id),
                    target TEXT REFERENCES grv_entities(id), kind TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS grv_edges_source ON grv_edges(source);
                CREATE INDEX IF NOT EXISTS grv_edges_target ON grv_edges(target);
                CREATE TABLE IF NOT EXISTS grv_edge_evidence(
                    edge_id TEXT REFERENCES grv_edges(id) ON DELETE CASCADE,
                    chunk_id TEXT REFERENCES grv_chunks(id) ON DELETE CASCADE,
                    PRIMARY KEY(edge_id,chunk_id)
                );
                CREATE INDEX IF NOT EXISTS grv_edge_evidence_chunk ON grv_edge_evidence(chunk_id);
            """)
        await self._run(create)

    @staticmethod
    def _scope(document_ids: list[str] | None, alias: str = "c") -> tuple[str, list[str]]:
        if document_ids is None:
            return "", []
        return f" AND {alias}.document_id IN ({','.join('?' for _ in document_ids) or 'NULL'})", document_ids

    def _prune(self) -> None:
        self.db.execute("DELETE FROM grv_edges WHERE NOT EXISTS(SELECT 1 FROM grv_edge_evidence p WHERE p.edge_id=grv_edges.id)")
        self.db.execute("DELETE FROM grv_entities WHERE NOT EXISTS(SELECT 1 FROM grv_mentions m WHERE m.entity_id=grv_entities.id)")

    async def replace_document(self, document_id: str, name: str, chunks: list[Chunk], extractions: list[ChunkExtraction]) -> dict:
        entities, mentions, edges = _facts(chunks, extractions)

        def replace():
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute("DELETE FROM grv_documents WHERE id=?", (document_id,))
                self.db.execute("INSERT INTO grv_documents(id,name) VALUES(?,?)", (document_id, name))
                self.db.executemany("INSERT INTO grv_chunks VALUES(?,?,?,?,?,?,?)", [
                    (c.id, document_id, name, c.text, c.page, c.position, json.dumps(c.embedding, allow_nan=False)) for c in chunks
                ])
                self.db.executemany("INSERT OR IGNORE INTO grv_entities VALUES(?,?,?)", [(e["id"], e["label"], e["kind"]) for e in entities])
                self.db.executemany("INSERT OR IGNORE INTO grv_mentions VALUES(?,?)", [(m["entity_id"], m["chunk_id"]) for m in mentions])
                self.db.executemany("INSERT OR IGNORE INTO grv_edges VALUES(?,?,?,?)", [(e["id"], e["source"], e["target"], e["kind"]) for e in edges])
                self.db.executemany("INSERT OR IGNORE INTO grv_edge_evidence VALUES(?,?)", [(e["id"], identifier) for e in edges for identifier in e["evidence_ids"]])
                self._prune()
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            return {"chunks": len(chunks), "entities": len(entities), "relationships": len(edges)}
        return await self._run(replace)

    async def delete(self, document_id: str) -> None:
        def remove():
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute("DELETE FROM grv_documents WHERE id=?", (document_id,))
                self._prune()
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        await self._run(remove)

    def _entity_ids(self, chunk_id: str) -> list[str]:
        return [row[0] for row in self.db.execute("SELECT entity_id FROM grv_mentions WHERE chunk_id=? ORDER BY entity_id", (chunk_id,))]

    async def vector_search(self, embedding: list[float], document_ids: list[str] | None, limit: int) -> list[Evidence]:
        def search():
            scope, params = self._scope(document_ids)
            rows = self.db.execute("SELECT * FROM grv_chunks c WHERE 1=1" + scope, params)
            scored = ((cosine(embedding, json.loads(row["embedding"])), dict(row)) for row in rows)
            best = heapq.nlargest(limit, scored, key=lambda item: (item[0], item[1]["id"]))
            return [_evidence(row, score, "vector", self._entity_ids(row["id"])) for score, row in best if score > 0.05]
        return await self._run(search)

    async def lexical_search(self, question: str, document_ids: list[str] | None, limit: int) -> list[Evidence]:
        def search():
            terms = words(question)
            if not terms:
                return []
            scope, params = self._scope(document_ids)
            rows = self.db.execute("SELECT * FROM grv_chunks c WHERE 1=1" + scope, params)
            scored = ((len(terms & words(row["text"])) / len(terms), dict(row)) for row in rows)
            best = heapq.nlargest(limit, scored, key=lambda item: (item[0], item[1]["id"]))
            return [_evidence(row, score, "lexical", self._entity_ids(row["id"])) for score, row in best if score > 0]
        return await self._run(search)

    async def graph_search(self, question: str, seed_chunk_ids: list[str], document_ids: list[str] | None, hops: int, limit: int) -> list[Evidence]:
        def search():
            scope, params = self._scope(document_ids)
            terms = sorted(words(question))[:30]
            conditions = ["lower(e.label) LIKE ?" for _ in terms]
            term_params = [f"%{term}%" for term in terms]
            query = (
                "SELECT DISTINCT e.id FROM grv_entities e JOIN grv_mentions m ON m.entity_id=e.id "
                "JOIN grv_chunks c ON c.id=m.chunk_id WHERE 1=1" + scope
            )
            roots = []
            if conditions:
                roots = [row[0] for row in self.db.execute(query + " AND (" + " OR ".join(conditions) + ") ORDER BY e.id LIMIT ?", params + term_params + [self.settings.max_graph_nodes])]
            if not roots and seed_chunk_ids:
                roots = [row[0] for row in self.db.execute(query + f" AND c.id IN ({','.join('?' for _ in seed_chunk_ids)}) ORDER BY e.id LIMIT ?", params + seed_chunk_ids + [self.settings.max_graph_nodes])]
            frontier, visited, proofs, seen_edges = set(roots), set(roots), {}, set()
            for depth in range(1, hops + 1):
                if not frontier or len(seen_edges) >= self.settings.max_graph_edges:
                    break
                marks = ",".join("?" for _ in frontier)
                values = sorted(frontier)
                rows = self.db.execute(
                    "SELECT e.id,e.source,e.target,p.chunk_id FROM grv_edges e "
                    "JOIN grv_edge_evidence p ON p.edge_id=e.id JOIN grv_chunks c ON c.id=p.chunk_id "
                    f"WHERE (e.source IN ({marks}) OR e.target IN ({marks}))" + scope + " ORDER BY e.id,p.chunk_id LIMIT ?",
                    values + values + params + [self.settings.max_graph_edges * 8],
                )
                following = set()
                for row in rows:
                    if row["id"] not in seen_edges and len(seen_edges) >= self.settings.max_graph_edges:
                        continue
                    seen_edges.add(row["id"])
                    if len(proofs) < limit or row["chunk_id"] in proofs:
                        proofs.setdefault(row["chunk_id"], depth)
                    for identifier in (row["source"], row["target"]):
                        if identifier not in visited and len(visited) < self.settings.max_graph_nodes:
                            visited.add(identifier)
                            following.add(identifier)
                frontier = following
            if not proofs:
                return []
            identifiers = list(proofs)
            rows = self.db.execute(
                f"SELECT * FROM grv_chunks c WHERE c.id IN ({','.join('?' for _ in identifiers)})" + scope,
                identifiers + params,
            )
            evidence = [_evidence(dict(row), 0.9 - 0.1 * proofs[row["id"]], "graph", self._entity_ids(row["id"])) for row in rows]
            return sorted(evidence, key=lambda item: (-item.score, item.id))[:limit]
        return await self._run(search)

    async def graph(self, document_ids: list[str] | None = None, limit: int = 100, evidence_ids: list[str] | None = None) -> GraphView:
        def read():
            scope, params = self._scope(document_ids)
            if evidence_ids is not None:
                scope += f" AND c.id IN ({','.join('?' for _ in evidence_ids) or 'NULL'})"
                params = params + evidence_ids
            nodes = [dict(row) for row in self.db.execute(
                "SELECT DISTINCT e.id,e.label,e.kind FROM grv_entities e JOIN grv_mentions m ON m.entity_id=e.id "
                "JOIN grv_chunks c ON c.id=m.chunk_id WHERE 1=1" + scope + " ORDER BY e.label,e.id LIMIT ?", params + [limit],
            )]
            if not nodes:
                return GraphView()
            identifiers = [node["id"] for node in nodes]
            marks = ",".join("?" for _ in identifiers)
            rows = self.db.execute(
                "SELECT e.id,e.source,e.target,e.kind,p.chunk_id FROM grv_edges e "
                "JOIN grv_edge_evidence p ON p.edge_id=e.id JOIN grv_chunks c ON c.id=p.chunk_id "
                f"WHERE e.source IN ({marks}) AND e.target IN ({marks})" + scope + " ORDER BY e.id,p.chunk_id LIMIT ?",
                identifiers + identifiers + params + [self.settings.max_graph_edges * 8],
            )
            edges = [{**dict(row), "evidence_ids": [row["chunk_id"]]} for row in rows]
            return _view(nodes, edges, limit, self.settings.max_graph_edges)
        return await self._run(read)

    async def stats(self) -> dict:
        def counts():
            return {key: self.db.execute(f"SELECT COUNT(*) FROM grv_{key}").fetchone()[0] for key in ("chunks", "entities", "edges")}
        result = await self._run(counts)
        result["relationships"] = result.pop("edges")
        return result

    async def close(self) -> None:
        def release():
            if self.connection is not None:
                self.connection.close()
                self.connection = None
        await self._run(release)


# Live graph relationships are typed from a fixed allowlist, never raw LLM Cypher.
TYPED_RELATIONSHIPS = "|".join(RELATION_TYPES)
VECTOR_INDEX = "grv_chunk_vector"


class Neo4jGraphStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.driver = None

    async def _read(self, query: str, **parameters) -> list[dict]:
        if self.driver is None:
            raise RuntimeError("Graph store is not initialized")
        from neo4j import Query
        async with self.driver.session(database=self.settings.neo4j_database) as session:
            result = await session.run(Query(query, timeout=self.settings.request_timeout_seconds), parameters)
            return await result.data()

    async def initialize(self) -> None:
        try:
            from neo4j import AsyncGraphDatabase
        except ImportError:
            raise RuntimeError("Live mode requires the neo4j Python driver") from None
        self.driver = AsyncGraphDatabase.driver(
            self.settings.neo4j_uri, auth=(self.settings.neo4j_username, self.settings.neo4j_password),
            connection_timeout=self.settings.request_timeout_seconds,
            connection_acquisition_timeout=self.settings.request_timeout_seconds,
            max_transaction_retry_time=self.settings.request_timeout_seconds,
            max_connection_pool_size=self.settings.provider_concurrency,
        )
        await self.driver.verify_connectivity()
        for label in ("GRVDocument", "GRVChunk", "GRVEntity"):
            await self._read(f"CREATE CONSTRAINT grv_{label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE")
        await self._read("CREATE INDEX grv_chunk_document IF NOT EXISTS FOR (c:GRVChunk) ON (c.document_id)")
        await self._read(
            "CREATE VECTOR INDEX grv_chunk_vector IF NOT EXISTS FOR (c:GRVChunk) ON (c.embedding) "
            "OPTIONS {indexConfig: {`vector.dimensions`: $dimensions, `vector.similarity_function`: 'cosine'}}",
            dimensions=self.settings.embedding_dimensions,
        )
        indexes = await self._read("SHOW VECTOR INDEXES YIELD name,options WHERE name=$name RETURN options", name=VECTOR_INDEX)
        if not indexes or indexes[0]["options"].get("indexConfig", {}).get("vector.dimensions") != self.settings.embedding_dimensions:
            raise RuntimeError("Neo4j vector index dimensions differ from EMBEDDING_DIMENSIONS")
        await self._read("CALL db.awaitIndex($name, $timeout)", name=VECTOR_INDEX, timeout=int(self.settings.request_timeout_seconds))

    async def _remove(self, tx, document_id: str) -> None:
        result = await tx.run("MATCH (c:GRVChunk {document_id:$document_id}) RETURN collect(c.id) AS ids", document_id=document_id)
        record = await result.single()
        old_ids = record["ids"] if record else []
        if old_ids:
            result = await tx.run(
                f"MATCH (:GRVEntity)-[r:{TYPED_RELATIONSHIPS}]->(:GRVEntity) WHERE any(id IN r.evidence_ids WHERE id IN $ids) "
                "SET r.evidence_ids=[id IN r.evidence_ids WHERE NOT id IN $ids] WITH r WHERE size(r.evidence_ids)=0 DELETE r",
                ids=old_ids,
            )
            await result.consume()
        result = await tx.run("MATCH (c:GRVChunk {document_id:$document_id}) DETACH DELETE c", document_id=document_id)
        await result.consume()
        result = await tx.run("MATCH (d:GRVDocument {id:$document_id}) DETACH DELETE d", document_id=document_id)
        await result.consume()

    async def _prune(self, tx) -> None:
        result = await tx.run("MATCH (e:GRVEntity) WHERE NOT EXISTS {MATCH (:GRVChunk)-[:MENTIONS]->(e)} DETACH DELETE e")
        await result.consume()

    async def replace_document(self, document_id: str, name: str, chunks: list[Chunk], extractions: list[ChunkExtraction]) -> dict:
        from neo4j import unit_of_work
        entities, mentions, edges = _facts(chunks, extractions)

        @unit_of_work(timeout=self.settings.request_timeout_seconds)
        async def replace(tx):
            await self._remove(tx, document_id)
            result = await tx.run("CREATE (:GRVDocument {id:$id,name:$name})", id=document_id, name=name)
            await result.consume()
            result = await tx.run(
                "MATCH (d:GRVDocument {id:$document_id}) UNWIND $chunks AS row "
                "CREATE (c:GRVChunk) SET c=row CREATE (d)-[:HAS_CHUNK]->(c)",
                document_id=document_id, chunks=[chunk.model_dump() for chunk in chunks],
            )
            await result.consume()
            result = await tx.run(
                "UNWIND $entities AS row MERGE (e:GRVEntity {id:row.id}) "
                "ON CREATE SET e.label=row.label,e.kind=row.kind", entities=entities,
            )
            await result.consume()
            result = await tx.run(
                "UNWIND $mentions AS row MATCH (c:GRVChunk {id:row.chunk_id}),(e:GRVEntity {id:row.entity_id}) "
                "MERGE (c)-[:MENTIONS]->(e)", mentions=mentions,
            )
            await result.consume()
            for kind in RELATION_TYPES:
                typed = [edge for edge in edges if edge["kind"] == kind]
                if not typed:
                    continue
                result = await tx.run(
                    "UNWIND $edges AS row MATCH (s:GRVEntity {id:row.source}),(t:GRVEntity {id:row.target}) "
                    f"MERGE (s)-[r:{kind} {{id:row.id}}]->(t) "
                    "SET r.evidence_ids=reduce(ids=coalesce(r.evidence_ids,[]),eid IN row.evidence_ids | "
                    "CASE WHEN eid IN ids THEN ids ELSE ids+[eid] END)", edges=typed,
                )
                await result.consume()
            await self._prune(tx)
            return {"chunks": len(chunks), "entities": len(entities), "relationships": len(edges)}
        async with self.driver.session(database=self.settings.neo4j_database) as session:
            return await session.execute_write(replace)

    async def delete(self, document_id: str) -> None:
        from neo4j import unit_of_work

        @unit_of_work(timeout=self.settings.request_timeout_seconds)
        async def remove(tx):
            await self._remove(tx, document_id)
            await self._prune(tx)
        async with self.driver.session(database=self.settings.neo4j_database) as session:
            await session.execute_write(remove)

    async def vector_search(self, embedding: list[float], document_ids: list[str] | None, limit: int) -> list[Evidence]:
        if document_ids is None:
            rows = await self._read(
                "CALL db.index.vector.queryNodes($index_name,$limit,$embedding) YIELD node,score "
                "RETURN properties(node) AS chunk,score ORDER BY score DESC", index_name=VECTOR_INDEX, limit=limit, embedding=embedding,
            )
        else:
            # Exact prefiltering prevents the global ANN shortlist from hiding scoped documents.
            rows = await self._read(
                "MATCH (c:GRVChunk) WHERE c.document_id IN $document_ids "
                "WITH c,vector.similarity.cosine(c.embedding,$embedding) AS score "
                "WHERE score IS NOT NULL RETURN properties(c) AS chunk,score ORDER BY score DESC,c.id LIMIT $limit",
                document_ids=document_ids, embedding=embedding, limit=limit,
            )
        evidence = [_evidence(row["chunk"], max(0, 2 * row["score"] - 1), "vector") for row in rows if row["score"] > 0.525]
        return await self._attach_entities(evidence)

    async def _attach_entities(self, evidence: list[Evidence]) -> list[Evidence]:
        if not evidence:
            return evidence
        rows = await self._read(
            "MATCH (c:GRVChunk)-[:MENTIONS]->(e:GRVEntity) WHERE c.id IN $ids "
            "RETURN c.id AS id,collect(DISTINCT e.id) AS entities", ids=[item.id for item in evidence],
        )
        mapping = {row["id"]: row["entities"] for row in rows}
        return [item.model_copy(update={"entity_ids": mapping.get(item.id, [])}) for item in evidence]

    async def lexical_search(self, question: str, document_ids: list[str] | None, limit: int) -> list[Evidence]:
        terms = sorted(words(question))[:50]
        if not terms:
            return []
        rows = await self._read(
            "MATCH (c:GRVChunk) WHERE ($document_ids IS NULL OR c.document_id IN $document_ids) "
            "AND any(term IN $terms WHERE toLower(c.text) CONTAINS term) "
            "WITH c,size([term IN $terms WHERE toLower(c.text) CONTAINS term]) AS matches "
            "RETURN properties(c) AS chunk,matches ORDER BY matches DESC,c.id LIMIT $limit",
            document_ids=document_ids, terms=terms, limit=limit,
        )
        evidence = []
        for row in rows:
            score = len(set(terms) & words(row["chunk"]["text"])) / len(terms)
            if score:
                evidence.append(_evidence(row["chunk"], score, "lexical"))
        return await self._attach_entities(evidence)

    async def graph_search(self, question: str, seed_chunk_ids: list[str], document_ids: list[str] | None, hops: int, limit: int) -> list[Evidence]:
        roots = await self._read(
            "MATCH (c:GRVChunk)-[:MENTIONS]->(e:GRVEntity) "
            "WHERE ($document_ids IS NULL OR c.document_id IN $document_ids) "
            "AND any(term IN $terms WHERE toLower(e.label) CONTAINS term) "
            "RETURN DISTINCT e.id AS id ORDER BY id LIMIT $limit",
            document_ids=document_ids, terms=sorted(words(question))[:30], limit=self.settings.max_graph_nodes,
        )
        if not roots and seed_chunk_ids:
            roots = await self._read(
                "MATCH (c:GRVChunk)-[:MENTIONS]->(e:GRVEntity) WHERE c.id IN $ids "
                "AND ($document_ids IS NULL OR c.document_id IN $document_ids) "
                "RETURN DISTINCT e.id AS id ORDER BY id LIMIT $limit", ids=seed_chunk_ids,
                document_ids=document_ids, limit=self.settings.max_graph_nodes,
            )
        frontier = {row["id"] for row in roots}
        visited, seen_edges, proofs = set(frontier), set(), {}
        for depth in range(1, hops + 1):
            if not frontier or len(seen_edges) >= self.settings.max_graph_edges:
                break
            rows = await self._read(
                f"MATCH (a:GRVEntity)-[r:{TYPED_RELATIONSHIPS}]-(b:GRVEntity) WHERE a.id IN $frontier "
                "AND EXISTS {MATCH (c:GRVChunk) WHERE c.id IN r.evidence_ids "
                "AND ($document_ids IS NULL OR c.document_id IN $document_ids)} "
                "WITH DISTINCT r ORDER BY r.id LIMIT $edge_limit "
                "UNWIND r.evidence_ids AS eid MATCH (c:GRVChunk {id:eid}) "
                "WHERE $document_ids IS NULL OR c.document_id IN $document_ids "
                "WITH r,collect(DISTINCT c.id)[..$proof_limit] AS proofs "
                "RETURN r.id AS id,startNode(r).id AS source,endNode(r).id AS target,proofs AS evidence_ids",
                frontier=sorted(frontier), document_ids=document_ids,
                edge_limit=self.settings.max_graph_edges, proof_limit=limit,
            )
            following = set()
            for row in rows:
                if row["id"] not in seen_edges and len(seen_edges) >= self.settings.max_graph_edges:
                    continue
                seen_edges.add(row["id"])
                for identifier in row["evidence_ids"]:
                    if len(proofs) < limit or identifier in proofs:
                        proofs.setdefault(identifier, depth)
                for identifier in (row["source"], row["target"]):
                    if identifier not in visited and len(visited) < self.settings.max_graph_nodes:
                        visited.add(identifier)
                        following.add(identifier)
            frontier = following
        if not proofs:
            return []
        rows = await self._read(
            "MATCH (c:GRVChunk) WHERE c.id IN $ids AND ($document_ids IS NULL OR c.document_id IN $document_ids) "
            "RETURN properties(c) AS chunk", ids=list(proofs), document_ids=document_ids,
        )
        evidence = [_evidence(row["chunk"], 0.9 - 0.1 * proofs[row["chunk"]["id"]], "graph") for row in rows]
        return await self._attach_entities(sorted(evidence, key=lambda item: (-item.score, item.id))[:limit])

    async def graph(self, document_ids: list[str] | None = None, limit: int = 100, evidence_ids: list[str] | None = None) -> GraphView:
        rows = await self._read(
            "MATCH (c:GRVChunk)-[:MENTIONS]->(e:GRVEntity) "
            "WHERE ($document_ids IS NULL OR c.document_id IN $document_ids) "
            "AND ($evidence_ids IS NULL OR c.id IN $evidence_ids) "
            "RETURN DISTINCT e.id AS id,e.label AS label,e.kind AS kind ORDER BY label,id LIMIT $limit",
            document_ids=document_ids, evidence_ids=evidence_ids, limit=limit,
        )
        if not rows:
            return GraphView()
        edges = await self._read(
            f"MATCH (s:GRVEntity)-[r:{TYPED_RELATIONSHIPS}]->(t:GRVEntity) "
            "WHERE s.id IN $node_ids AND t.id IN $node_ids "
            "AND EXISTS {MATCH (c:GRVChunk) WHERE c.id IN r.evidence_ids "
            "AND ($document_ids IS NULL OR c.document_id IN $document_ids) "
            "AND ($evidence_ids IS NULL OR c.id IN $evidence_ids)} "
            "WITH s,r,t ORDER BY r.id LIMIT $edge_limit "
            "UNWIND r.evidence_ids AS eid MATCH (c:GRVChunk {id:eid}) "
            "WHERE ($document_ids IS NULL OR c.document_id IN $document_ids) "
            "AND ($evidence_ids IS NULL OR c.id IN $evidence_ids) "
            "RETURN r.id AS id,s.id AS source,t.id AS target,type(r) AS kind,collect(DISTINCT c.id)[..1000] AS evidence_ids",
            node_ids=[row["id"] for row in rows], document_ids=document_ids, evidence_ids=evidence_ids,
            edge_limit=self.settings.max_graph_edges,
        )
        return _view(rows, edges, limit, self.settings.max_graph_edges)

    async def stats(self) -> dict:
        rows = await self._read(
            "CALL {MATCH (c:GRVChunk) RETURN count(c) AS chunks} "
            "CALL {MATCH (e:GRVEntity) RETURN count(e) AS entities} "
            f"CALL {{MATCH (:GRVEntity)-[r:{TYPED_RELATIONSHIPS}]->(:GRVEntity) RETURN count(r) AS relationships}} "
            "RETURN chunks,entities,relationships"
        )
        return rows[0]

    async def close(self) -> None:
        if self.driver is not None:
            await self.driver.close()
            self.driver = None
