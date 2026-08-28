from domain.ports.memory_repository import MemoryRepository


class PinMemoryUseCase:
    def __init__(self, repository: MemoryRepository) -> None:
        self._repository = repository

    async def execute(self, memory_id: str, *, pinned: bool = True) -> None:
        if pinned:
            await self._repository.pin(memory_id)
        else:
            await self._repository.unpin(memory_id)
