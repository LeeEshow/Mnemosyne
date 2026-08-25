from dataclasses import dataclass
from datetime import UTC, datetime

import config
from domain.models import Memory, MemoryStatusFilter, ScoredMemory
from domain.ports.embedding_provider import EmbeddingProvider
from domain.ports.gate_classifier import GateClassifier
from domain.ports.memory_repository import MemoryRepository
from domain.write_gate_policy import GateVerdict, WriteGateDecision, decide_preliminary


@dataclass(frozen=True)
class SaveMemoryRequest:
    domain: str
    title: str
    context: str
    type: str
    tags: tuple[str, ...] | None = None
    source_id: str | None = None
    importance_score: int | None = None


@dataclass(frozen=True)
class SaveMemoryResult:
    decision: WriteGateDecision
    memory_id: str | None


class SaveMemoryUseCase:
    def __init__(
        self,
        repository: MemoryRepository,
        embedding_provider: EmbeddingProvider,
        gate_classifier: GateClassifier,
    ) -> None:
        self._repository = repository
        self._embedding_provider = embedding_provider
        self._gate_classifier = gate_classifier

    async def execute(self, request: SaveMemoryRequest) -> SaveMemoryResult:
        embedding = await self._embedding_provider.embed(request.context)
        nearest = await self._find_nearest_candidate(request.domain, embedding)
        preliminary = decide_preliminary(request.context, nearest, config.LOW_THRESHOLD)
        if preliminary == WriteGateDecision.NOOP:
            return await self._apply_noop(nearest)
        if preliminary == WriteGateDecision.ADD:
            return await self._apply_add(request, embedding)
        new_memory = self._build_memory(request, embedding)
        verdict = await self._gate_classifier.classify(new_memory, nearest.memory)
        return await self._apply_verdict(verdict, nearest, request, embedding)

    async def _find_nearest_candidate(
        self, domain: str, embedding: tuple[float, ...]
    ) -> ScoredMemory | None:
        candidates = await self._repository.find_nearest(
            domain, embedding, config.WRITE_GATE_CANDIDATE_LIMIT, MemoryStatusFilter()
        )
        return candidates[0] if candidates else None

    async def _apply_noop(self, nearest: ScoredMemory) -> SaveMemoryResult:
        await self._repository.record_access(nearest.memory.id)
        return SaveMemoryResult(WriteGateDecision.NOOP, nearest.memory.id)

    async def _apply_add(
        self, request: SaveMemoryRequest, embedding: tuple[float, ...]
    ) -> SaveMemoryResult:
        saved = await self._repository.save(self._build_memory(request, embedding))
        return SaveMemoryResult(WriteGateDecision.ADD, saved.id)

    async def _apply_verdict(
        self,
        verdict: GateVerdict,
        nearest: ScoredMemory,
        request: SaveMemoryRequest,
        embedding: tuple[float, ...],
    ) -> SaveMemoryResult:
        if verdict.decision == WriteGateDecision.NOOP:
            return await self._apply_noop(nearest)
        if verdict.decision == WriteGateDecision.UPDATE:
            return await self._apply_update(verdict, nearest)
        if verdict.decision == WriteGateDecision.SUPERSEDE:
            return await self._apply_supersede(request, embedding, nearest.memory.id)
        return await self._apply_add(request, embedding)

    async def _apply_update(self, verdict: GateVerdict, nearest: ScoredMemory) -> SaveMemoryResult:
        title = verdict.merged_title or nearest.memory.title
        context = verdict.merged_context or nearest.memory.context
        embedding = await self._embedding_provider.embed(context)
        await self._repository.overwrite_content(nearest.memory.id, title, context, embedding)
        return SaveMemoryResult(WriteGateDecision.UPDATE, nearest.memory.id)

    async def _apply_supersede(
        self, request: SaveMemoryRequest, embedding: tuple[float, ...], superseded_id: str
    ) -> SaveMemoryResult:
        saved = await self._repository.save(self._build_memory(request, embedding))
        await self._repository.mark_superseded(superseded_id, saved.id)
        return SaveMemoryResult(WriteGateDecision.SUPERSEDE, saved.id)

    def _build_memory(self, request: SaveMemoryRequest, embedding: tuple[float, ...]) -> Memory:
        return Memory(
            type=request.type,
            domain=request.domain,
            title=request.title,
            context=request.context,
            embedding=embedding,
            created_at=datetime.now(UTC),
            importance_score=request.importance_score or config.DEFAULT_IMPORTANCE_SCORE,
            tags=request.tags,
            source_id=request.source_id,
        )
