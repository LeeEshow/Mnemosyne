from application.superseded_resolution import resolve_active_memory_id
from domain.ports.memory_repository import MemoryRepository


class PinMemoryUseCase:
    def __init__(self, repository: MemoryRepository) -> None:
        self._repository = repository

    async def execute(self, memory_id: str, *, pinned: bool = True) -> None:
        active_id = await resolve_active_memory_id(self._repository, memory_id)
        if pinned:
            await self._repository.pin(active_id)
        else:
            await self._repository.unpin(active_id)
