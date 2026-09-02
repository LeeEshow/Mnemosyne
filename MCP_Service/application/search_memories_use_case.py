import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone

import config
from application.domain_validation import ensure_domain_registered
from domain.models import Memory, MemoryStatusFilter, ScoredMemory
from domain.ports.domain_repository import DomainRepository
from domain.ports.embedding_provider import EmbeddingProvider
from domain.ports.memory_repository import MemoryRepository
from domain.scoring import ScoringParameters, ScoringWeights, calculate_decay_score

_EXACT_TAG_MATCH_SIMILARITY = 1.0

_SCORING_WEIGHTS = ScoringWeights(
    relevance=config.WEIGHT_RELEVANCE,
    importance=config.WEIGHT_IMPORTANCE,
    recency=config.WEIGHT_RECENCY,
    access_frequency=config.WEIGHT_ACCESS_FREQUENCY,
)


@dataclass(frozen=True)
class SearchMemoriesRequest:
    domain: str
    query: str
    type: str | None = None
    exact_tags: tuple[str, ...] | None = None
    limit: int = config.SEARCH_MEMORIES_DEFAULT_LIMIT
    include_superseded: bool = False
    include_archived: bool = False
    record_access: bool = True


@dataclass(frozen=True)
class SearchMemoriesResult:
    memories: tuple[Memory, ...]


class SearchMemoriesUseCase:
    def __init__(
        self,
        repository: MemoryRepository,
        embedding_provider: EmbeddingProvider,
        domain_repository: DomainRepository,
    ) -> None:
        self._repository = repository
        self._embedding_provider = embedding_provider
        self._domain_repository = domain_repository

    async def execute(self, request: SearchMemoriesRequest) -> SearchMemoriesResult:
        domain = await ensure_domain_registered(self._domain_repository, request.domain)
        request = replace(request, domain=domain)
        status_filter = MemoryStatusFilter(request.include_superseded, request.include_archived)
        candidates = await self._gather_candidates(request, status_filter)
        filtered = self._apply_type_filter(candidates, request.type)
        top = self._rank_top(filtered, request.limit)
        if request.record_access:
            await self._record_access(top)
        return SearchMemoriesResult(tuple(scored.memory for scored in top))

    async def _gather_candidates(
        self, request: SearchMemoriesRequest, status_filter: MemoryStatusFilter
    ) -> dict[str, ScoredMemory]:
        embedding = await self._embedding_provider.embed(request.query)
        vector_hits = await self._gather_vector_track(request.domain, embedding, status_filter)
        tag_hits = await self._gather_tag_track(request.domain, request.exact_tags, status_filter)
        return self._merge(vector_hits, tag_hits)

    async def _gather_vector_track(
        self, domain: str, embedding: tuple[float, ...], status_filter: MemoryStatusFilter
    ) -> list[ScoredMemory]:
        domain_task = self._repository.find_nearest(
            domain, embedding, config.VECTOR_SEARCH_K_DOMAIN, status_filter
        )
        global_task = self._repository.find_nearest(
            config.GLOBAL_DOMAIN, embedding, config.VECTOR_SEARCH_K_GLOBAL, status_filter
        )
        domain_hits, global_hits = await asyncio.gather(domain_task, global_task)
        return [*domain_hits, *global_hits]

    async def _gather_tag_track(
        self, domain: str, exact_tags: tuple[str, ...] | None, status_filter: MemoryStatusFilter
    ) -> list[Memory]:
        if not exact_tags:
            return []
        domain_task = self._repository.find_by_tags(domain, exact_tags, status_filter)
        global_task = self._repository.find_by_tags(config.GLOBAL_DOMAIN, exact_tags, status_filter)
        domain_hits, global_hits = await asyncio.gather(domain_task, global_task)
        return [*domain_hits, *global_hits]

    def _merge(
        self, vector_hits: list[ScoredMemory], tag_hits: list[Memory]
    ) -> dict[str, ScoredMemory]:
        merged = {scored.memory.id: scored for scored in vector_hits}
        for memory in tag_hits:
            merged[memory.id] = ScoredMemory(memory, _EXACT_TAG_MATCH_SIMILARITY)
        return merged

    def _apply_type_filter(
        self, candidates: dict[str, ScoredMemory], type_filter: str | None
    ) -> list[ScoredMemory]:
        values = list(candidates.values())
        if type_filter is None:
            return values
        normalized_filter = type_filter.strip().lower()
        return [scored for scored in values if scored.memory.type.strip().lower() == normalized_filter]

    def _rank_top(self, candidates: list[ScoredMemory], limit: int) -> list[ScoredMemory]:
        parameters = ScoringParameters(_SCORING_WEIGHTS, config.DECAY_LAMBDA, config.ACCESS_FREQUENCY_LOG_CAP)
        now = datetime.now(timezone.utc)
        ranked = sorted(candidates, key=lambda c: calculate_decay_score(c, now, parameters), reverse=True)
        return ranked[:limit]

    async def _record_access(self, top: list[ScoredMemory]) -> None:
        await asyncio.gather(*(self._repository.record_access(scored.memory.id) for scored in top))
