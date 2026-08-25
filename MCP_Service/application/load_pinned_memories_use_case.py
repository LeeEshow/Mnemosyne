import asyncio
from dataclasses import dataclass

import config
from domain.models import Memory
from domain.ports.memory_repository import MemoryRepository


@dataclass(frozen=True)
class LoadPinnedMemoriesRequest:
    domain: str
    limit: int = config.PINNED_MEMORIES_DEFAULT_LIMIT


class LoadPinnedMemoriesUseCase:
    def __init__(self, repository: MemoryRepository) -> None:
        self._repository = repository

    async def execute(self, request: LoadPinnedMemoriesRequest) -> tuple[Memory, ...]:
        domain_task = self._repository.find_pinned(request.domain)
        global_task = self._repository.find_pinned(config.GLOBAL_DOMAIN)
        domain_hits, global_hits = await asyncio.gather(domain_task, global_task)
        merged = self._dedupe(domain_hits, global_hits)
        ranked = sorted(
            merged, key=lambda memory: (memory.importance_score, memory.created_at), reverse=True
        )
        return tuple(ranked[: request.limit])

    def _dedupe(self, domain_hits: list[Memory], global_hits: list[Memory]) -> list[Memory]:
        by_id = {memory.id: memory for memory in [*domain_hits, *global_hits]}
        return list(by_id.values())
