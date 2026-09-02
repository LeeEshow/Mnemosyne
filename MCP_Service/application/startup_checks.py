import config
from domain.exceptions import ConfigurationError
from domain.ports.memory_repository import MemoryRepository


async def verify_stored_embedding_dimension(repository: MemoryRepository) -> None:
    sample = await repository.sample_one()
    if sample is None:
        return
    actual_dimension = len(sample.embedding)
    if actual_dimension == config.EMBEDDING_DIMENSION:
        return
    raise ConfigurationError(
        f"既有記憶的 embedding 實際維度為 {actual_dimension}，"
        f"與目前 config.EMBEDDING_DIMENSION={config.EMBEDDING_DIMENSION} 不符，"
        "可能是目前的 embedding 模型/GEMINI_API_KEY 設定跟寫入當時不一致，請檢查部署環境變數。"
    )
