import contextvars

from interface.asgi_query import get_query_param

_DOMAIN_QUERY_PARAM = "domain"

_domain_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mnemosyne_domain", default=None
)


class DomainBindingMiddleware:
    """ASGI 中介層：在每個請求開始時，把 query string 的 domain 值綁進 contextvars。

    只在 SSE 連線建立的 GET 請求（帶原始 query string）當下才有意義；
    POST /messages/ 只帶 session_id，此時綁定的值會是 None，但無妨——
    真正需要跨連線生命週期沿用的綁定，是由 `interface/mcp_server.py`
    的 `_bind_connection_domain`（MCPServer 的 lifespan）在 GET 請求當下
    讀走這裡的值，經 `lifespan_context` 傳給該連線後續所有工具呼叫；
    詳細原因見該函式的說明。
    """

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        token = _domain_var.set(get_query_param(scope, _DOMAIN_QUERY_PARAM))
        try:
            await self._app(scope, receive, send)
        finally:
            _domain_var.reset(token)


def current_domain() -> str | None:
    return _domain_var.get()
