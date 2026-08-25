from google import genai

import config


class VertexEmbeddingProvider:
    def __init__(self) -> None:
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
