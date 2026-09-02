import os

from google import genai
from google.genai import types

import config
from domain.exceptions import ConfigurationError

# text-multilingual-embedding-002（GEMINI_API_KEY 未設定時的 Vertex AI fallback 模型）固定輸出
# 768 維，不支援 output_dimensionality 截斷，跟 gemini-embedding-001 那條路徑不同。
_VERTEX_FALLBACK_MODEL_NATIVE_DIMENSION = 768


class VertexEmbeddingProvider:
    def __init__(self) -> None:
        # .strip() 而非直接 bool()：純空白字元的環境變數（例如 .env 誤寫成 GEMINI_API_KEY=" "）
        # 在 Python 中 bool(" ") 仍是 True，會讓下面的維度防呆誤判成「API Key 模式」而被繞過，
        # 直到第一次呼叫才在 Google API 端噴出認證失敗，失去 fail-fast 的意義。
        api_key = (os.environ.get("GEMINI_API_KEY") or "").strip() or None
        self._using_api_key = api_key is not None
        if api_key:
            # 個人 Google AI Studio API Key，走計量計費以外的個人訂閱額度。
            self._client = genai.Client(api_key=api_key)
        else:
            self._ensure_fallback_dimension_matches()
            self._client = genai.Client(
                vertexai=True,
                project=config.GOOGLE_CLOUD_PROJECT_ID,
                location=config.GOOGLE_CLOUD_LOCATION,
            )

    def _ensure_fallback_dimension_matches(self) -> None:
        if config.EMBEDDING_DIMENSION == _VERTEX_FALLBACK_MODEL_NATIVE_DIMENSION:
            return
        raise ConfigurationError(
            f"GEMINI_API_KEY 未設定，退回 Vertex AI 模型 {config.EMBEDDING_MODEL}"
            f"（固定輸出 {_VERTEX_FALLBACK_MODEL_NATIVE_DIMENSION} 維，不支援截斷），"
            f"但 config.EMBEDDING_DIMENSION={config.EMBEDDING_DIMENSION}，兩者不符，"
            "寫入/查詢會在 Firestore 向量索引層失敗。請確認是否漏設 GEMINI_API_KEY。"
        )

    async def embed(self, text: str) -> tuple[float, ...]:
        embed_config = (
            types.EmbedContentConfig(output_dimensionality=config.EMBEDDING_DIMENSION)
            if self._using_api_key
            else None
        )
        response = await self._client.aio.models.embed_content(
            model=config.EMBEDDING_MODEL, contents=text, config=embed_config
        )
        return tuple(response.embeddings[0].values)
