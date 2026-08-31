import asyncio
from dataclasses import replace

from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.document import DocumentSnapshot
from google.cloud.firestore_v1.vector import Vector

import config
from domain.models import Memory, MemoryContentUpdate, MemoryStatus, MemoryStatusFilter, ScoredMemory
from infrastructure import firebase_app

_DISTANCE_FIELD = "_vector_distance"


class FirestoreMemoryRepository:
    def __init__(self) -> None:
        firebase_app.ensure_initialized()
        self._collection = firestore.client().collection(config.MEMORIES_COLLECTION_NAME)

    async def save(self, memory: Memory) -> Memory:
        _, doc_ref = await asyncio.to_thread(self._collection.add, self._to_document(memory))
        return replace(memory, id=doc_ref.id)

    async def get_by_id(self, memory_id: str) -> Memory | None:
        snapshot = await asyncio.to_thread(self._collection.document(memory_id).get)
        return self._to_memory(snapshot) if snapshot.exists else None

    async def find_nearest(
        self, domain: str, embedding: tuple[float, ...], limit: int, status_filter: MemoryStatusFilter
    ) -> list[ScoredMemory]:
        batches = await asyncio.gather(
            *(
                asyncio.to_thread(self._find_nearest_by_status, domain, embedding, limit, status)
                for status in status_filter.allowed_statuses()
            )
        )
        merged = [scored for batch in batches for scored in batch]
        merged.sort(key=lambda scored: scored.similarity, reverse=True)
        return merged[:limit]

    async def find_by_tags(
        self, domain: str, tags: tuple[str, ...], status_filter: MemoryStatusFilter
    ) -> list[Memory]:
        snapshots = await asyncio.to_thread(self._query_by_tags, tags)
        allowed_statuses = status_filter.allowed_statuses()
        memories = (self._to_memory(snapshot) for snapshot in snapshots)
        return [
            memory
            for memory in memories
            if memory.domain == domain and memory.status in allowed_statuses
        ]

    async def find_pinned(self, domain: str) -> list[Memory]:
        snapshots = await asyncio.to_thread(self._query_pinned_by_domain, domain)
        return [self._to_memory(snapshot) for snapshot in snapshots]

    async def overwrite_content(self, memory_id: str, update: MemoryContentUpdate) -> None:
        await self._update(
            memory_id,
            {
                "title": update.title,
                "premise": update.premise,
                "conclusion": update.conclusion,
                "tags": list(update.tags) if update.tags else [],
                "embedding": Vector(update.embedding),
            },
        )

    async def mark_superseded(self, memory_id: str, superseded_by: str) -> None:
        await self._update(
            memory_id, {"status": MemoryStatus.SUPERSEDED.value, "superseded_by": superseded_by}
        )

    async def set_status(self, memory_id: str, status: MemoryStatus) -> None:
        await self._update(memory_id, {"status": status.value})

    async def delete(self, memory_id: str) -> None:
        await asyncio.to_thread(self._collection.document(memory_id).delete)

    async def pin(self, memory_id: str) -> None:
        await self._update(memory_id, {"is_pinned": True})

    async def unpin(self, memory_id: str) -> None:
        await self._update(memory_id, {"is_pinned": False})

    async def record_access(self, memory_id: str) -> None:
        increment = firestore.Increment(1)
        await self._update(memory_id, {"access_count": increment})

    async def _update(self, memory_id: str, fields: dict) -> None:
        await asyncio.to_thread(self._collection.document(memory_id).update, fields)

    def _find_nearest_by_status(
        self, domain: str, embedding: tuple[float, ...], limit: int, status: MemoryStatus
    ) -> list[ScoredMemory]:
        query = self._collection.where(filter=FieldFilter("domain", "==", domain)).where(
            filter=FieldFilter("status", "==", status.value)
        )
        vector_query = query.find_nearest(
            vector_field="embedding",
            query_vector=Vector(embedding),
            limit=limit,
            distance_measure=DistanceMeasure.COSINE,
            distance_result_field=_DISTANCE_FIELD,
        )
        return [self._to_scored_memory(snapshot) for snapshot in vector_query.get()]

    def _query_by_tags(self, tags: tuple[str, ...]) -> list[DocumentSnapshot]:
        limited_tags = list(tags[: config.FIRESTORE_ARRAY_CONTAINS_ANY_LIMIT])
        query = self._collection.where(filter=FieldFilter("tags", "array_contains_any", limited_tags))
        return list(query.get())

    def _query_pinned_by_domain(self, domain: str) -> list[DocumentSnapshot]:
        query = (
            self._collection.where(filter=FieldFilter("domain", "==", domain))
            .where(filter=FieldFilter("is_pinned", "==", True))
            .where(filter=FieldFilter("status", "==", MemoryStatus.ACTIVE.value))
        )
        return list(query.get())

    def _to_document(self, memory: Memory) -> dict:
        return {
            "type": memory.type,
            "domain": memory.domain,
            "title": memory.title,
            "premise": memory.premise,
            "conclusion": memory.conclusion,
            "embedding": Vector(memory.embedding),
            "created_at": memory.created_at,
            "importance_score": memory.importance_score,
            "is_pinned": memory.is_pinned,
            "status": memory.status.value,
            "tags": list(memory.tags) if memory.tags else [],
            "superseded_by": memory.superseded_by,
            "access_count": memory.access_count or 0,
        }

    def _to_memory(self, snapshot: DocumentSnapshot) -> Memory:
        data = snapshot.to_dict()
        return Memory(
            id=snapshot.id,
            type=data["type"],
            domain=data["domain"],
            title=data["title"],
            premise=data["premise"],
            conclusion=data["conclusion"],
            embedding=tuple(data["embedding"]),
            created_at=data["created_at"],
            importance_score=data["importance_score"],
            is_pinned=data["is_pinned"],
            status=MemoryStatus(data["status"]),
            tags=tuple(data.get("tags") or ()),
            superseded_by=data.get("superseded_by"),
            access_count=data.get("access_count"),
        )

    def _to_scored_memory(self, snapshot: DocumentSnapshot) -> ScoredMemory:
        distance = snapshot.get(_DISTANCE_FIELD)
        return ScoredMemory(memory=self._to_memory(snapshot), similarity=1 - distance)
