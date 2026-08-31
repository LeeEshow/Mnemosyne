from domain.models import MemoryStatus
from domain.ports.memory_repository import MemoryRepository


async def resolve_active_memory_id(repository: MemoryRepository, memory_id: str) -> str:
    current_id = memory_id
    visited: set[str] = set()
    while current_id not in visited:
        visited.add(current_id)
        memory = await repository.get_by_id(current_id)
        if memory is None or memory.status != MemoryStatus.SUPERSEDED or not memory.superseded_by:
            return current_id
        current_id = memory.superseded_by
    return current_id
