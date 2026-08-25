from domain.ports.memory_repository import MemoryRepository


class PinMemoryUseCase:
    def __init__(self, repository: MemoryRepository) -> None:
        self._repository = repository

    async def execute(self, memory_id: str) -> None:
        await self._repository.pin(memory_id)


class UnpinMemoryUseCase:
    def __init__(self, repository: MemoryRepository) -> None:
        self._repository = repository

    async def execute(self, memory_id: str) -> None:
        await self._repository.unpin(memory_id)
