import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache

from mcp.server import MCPServer
from mcp.server.mcpserver import Context

import config
from application.forget_memory_use_case import ForgetMemoryRequest, ForgetMemoryUseCase
from application.load_pinned_memories_use_case import (
    LoadPinnedMemoriesRequest,
    LoadPinnedMemoriesUseCase,
)
from application.pin_memory_use_case import PinMemoryUseCase, UnpinMemoryUseCase
from application.save_memory_use_case import SaveMemoryRequest, SaveMemoryUseCase
from application.search_memories_use_case import SearchMemoriesRequest, SearchMemoriesUseCase
from domain.models import Memory
from infrastructure.firestore_memory_repository import FirestoreMemoryRepository
from infrastructure.gemini_gate_classifier import GeminiGateClassifier
from infrastructure.vertex_embedding_provider import VertexEmbeddingProvider
from interface import connection_context, tool_schemas


@asynccontextmanager
async def _bind_connection_domain(server: MCPServer) -> AsyncIterator[str | None]:
    """連線建立當下（SSE GET 請求）綁定一次 domain，同連線後續所有工具呼叫沿用此值。

    MCP SSE transport 對每則訊息會用該訊息「送入」時的 context 覆寫執行環境
    （見 mcp.server.runner._sender_context），POST /messages/ 只帶 session_id、
    不帶 domain，所以不能靠 contextvars 在工具呼叫當下重讀 query string；
    lifespan 則是連線建立時進入一次、其回傳值經由 ctx.request_context.lifespan_context
    傳給該連線所有後續請求，因此改在這裡讀取當下 contextvars 綁定的 domain。
    """
    yield connection_context.current_domain()


mcp_server = MCPServer("mnemosyne", lifespan=_bind_connection_domain)


@dataclass(frozen=True)
class _Dependencies:
    save_memory: SaveMemoryUseCase
    search_memories: SearchMemoriesUseCase
    forget_memory: ForgetMemoryUseCase
    pin_memory: PinMemoryUseCase
    unpin_memory: UnpinMemoryUseCase
    load_pinned_memories: LoadPinnedMemoriesUseCase


@lru_cache(maxsize=1)
def _dependencies() -> _Dependencies:
    repository = FirestoreMemoryRepository()
    embedding_provider = VertexEmbeddingProvider()
    gate_classifier = GeminiGateClassifier()
    return _Dependencies(
        save_memory=SaveMemoryUseCase(repository, embedding_provider, gate_classifier),
        search_memories=SearchMemoriesUseCase(repository, embedding_provider),
        forget_memory=ForgetMemoryUseCase(repository),
        pin_memory=PinMemoryUseCase(repository),
        unpin_memory=UnpinMemoryUseCase(repository),
        load_pinned_memories=LoadPinnedMemoriesUseCase(repository),
    )


def _resolve_domain(ctx: Context) -> str:
    domain = ctx.request_context.lifespan_context or os.environ.get("MNEMOSYNE_DOMAIN")
    if domain is None:
        raise RuntimeError("無法解析 domain：連線 URL 缺少 domain query string，且未設定 MNEMOSYNE_DOMAIN")
    return domain


def _to_memory_view(memory: Memory) -> tool_schemas.MemoryView:
    return tool_schemas.MemoryView(
        doc_id=memory.id,
        type=memory.type,
        title=memory.title,
        context=memory.context,
        tags=list(memory.tags) if memory.tags else [],
        importance_score=memory.importance_score,
    )


@mcp_server.tool(
    description=(
        "當對話中出現值得長期保存的新事實、重要決策、個人偏好或代碼知識點時呼叫。"
        "已內建重複偵測與合併更新機制，若只是要新增資訊，呼叫前不需要先以 search_memories 確認是否重複。"
        "如果是任務完成後的經驗反思，請改用 reflect_on_task。"
        "注意：寫入範圍將自動被綁定在當前連線的領域下。"
    )
)
async def save_memory(
    ctx: Context,
    title: tool_schemas.SaveMemoryTitle,
    context: tool_schemas.SaveMemoryContext,
    type: tool_schemas.SaveMemoryType,
    tags: tool_schemas.SaveMemoryTags = None,
    source_id: tool_schemas.SaveMemorySourceId = None,
    importance_score: tool_schemas.SaveMemoryImportanceScore = None,
) -> tool_schemas.SaveMemoryResponse:
    request = SaveMemoryRequest(
        domain=_resolve_domain(ctx),
        title=title,
        context=context,
        type=type,
        tags=tuple(tags) if tags else None,
        source_id=source_id,
        importance_score=importance_score,
    )
    result = await _dependencies().save_memory.execute(request)
    return tool_schemas.SaveMemoryResponse(decision=result.decision.value, doc_id=result.memory_id)


@mcp_server.tool(
    description=(
        "當使用者的提問涉及過去的討論、決策、偏好，或你需要當前對話沒有的歷史脈絡時呼叫。"
        "注意：你的檢索範圍已自動鎖定於當前連線的領域與全域通用偏好（例如回覆語言格式），"
        "查無結果不代表使用者從未提過，可能只是超出目前可讀取的範圍。"
        "若查詢包含特定的錯誤代碼、函式名、股票代號等精確字串，請務必填入 exact_tags 參數以確保字面精確命中。"
    )
)
async def search_memories(
    ctx: Context,
    query: tool_schemas.SearchMemoriesQuery,
    type: tool_schemas.SearchMemoriesType = None,
    exact_tags: tool_schemas.SearchMemoriesExactTags = None,
    limit: tool_schemas.SearchMemoriesLimit = 3,
    include_superseded: tool_schemas.SearchMemoriesIncludeSuperseded = False,
    include_archived: tool_schemas.SearchMemoriesIncludeArchived = False,
) -> tool_schemas.SearchMemoriesResponse:
    request = SearchMemoriesRequest(
        domain=_resolve_domain(ctx),
        query=query,
        type=type,
        exact_tags=tuple(exact_tags) if exact_tags else None,
        limit=limit,
        include_superseded=include_superseded,
        include_archived=include_archived,
    )
    result = await _dependencies().search_memories.execute(request)
    return tool_schemas.SearchMemoriesResponse(memories=[_to_memory_view(m) for m in result.memories])


@mcp_server.tool(
    description=(
        "將某筆不再正確或已無用的記憶進行封存或刪除。"
        "你必須先使用 search_memories 檢索該記憶，以取得其 doc_id 後才能呼叫此工具。"
    )
)
async def forget_memory(
    doc_id: tool_schemas.ForgetMemoryDocId,
    hard_delete: tool_schemas.ForgetMemoryHardDelete = False,
) -> None:
    request = ForgetMemoryRequest(memory_id=doc_id, hard_delete=hard_delete)
    await _dependencies().forget_memory.execute(request)


@mcp_server.tool(
    description=(
        "將某筆記憶標記為常駐記憶，確保其之後一定會出現在對話開頭的常駐清單（load_pinned_memories）中。"
        "只在該記憶被判定為『極端重要、不能被一般排序稀釋』時使用，避免濫用造成常駐清單膨脹。"
        "你必須先使用 search_memories 檢索該記憶，以取得其 doc_id 後才能呼叫。"
    )
)
async def pin_memory(doc_id: tool_schemas.PinMemoryDocId) -> None:
    await _dependencies().pin_memory.execute(doc_id)


@mcp_server.tool(
    description=(
        "取消某筆記憶的常駐標記。"
        "你必須先知道該記憶的 doc_id（可透過 search_memories 或既有的常駐清單得知）才能呼叫。"
    )
)
async def unpin_memory(doc_id: tool_schemas.PinMemoryDocId) -> None:
    await _dependencies().unpin_memory.execute(doc_id)


@mcp_server.tool(
    description="對話開始時呼叫一次，取得少量常駐記憶直接帶入上下文；不需要在同一次對話中重複呼叫多次。"
)
async def load_pinned_memories(
    ctx: Context,
    limit: tool_schemas.LoadPinnedMemoriesLimit = 5,
) -> tool_schemas.SearchMemoriesResponse:
    request = LoadPinnedMemoriesRequest(domain=_resolve_domain(ctx), limit=limit)
    memories = await _dependencies().load_pinned_memories.execute(request)
    return tool_schemas.SearchMemoriesResponse(memories=[_to_memory_view(m) for m in memories])


app = connection_context.DomainBindingMiddleware(mcp_server.sse_app())


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.SERVER_PORT)


if __name__ == "__main__":
    main()
