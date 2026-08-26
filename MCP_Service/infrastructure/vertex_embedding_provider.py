import os

from google import genai

import config


class VertexEmbeddingProvider:
    def __init__(self) -> None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if api_key:
            # 個人 Google AI Studio API Key，走計量計費以外的個人訂閱額度。
            self._client = genai.Client(api_key=api_key)
        else:
            self._client = genai.Client(
                vertexai=True,
                project=config.GOOGLE_CLOUD_PROJECT_ID,
                location=config.GOOGLE_CLOUD_LOCATION,
            )

    async def embed(self, text: str) -> tuple[float, ...]:
        response = await self._client.aio.models.embed_content(
            model=config.EMBEDDING_MODEL, contents=text
        )
        return tuple(response.embeddings[0].values)
