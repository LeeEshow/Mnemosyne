from typing import Protocol


class EmbeddingProvider(Protocol):
    async def embed(self, text: str) -> tuple[float, ...]: ...
