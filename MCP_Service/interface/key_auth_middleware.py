import hmac

from interface.asgi_query import get_query_param

_KEY_QUERY_PARAM = "key"
_SESSION_ID_QUERY_PARAM = "session_id"
_UNAUTHORIZED_STATUS = 401


class KeyAuthMiddleware:
    """ASGI 中介層：在建立 SSE 連線的請求上驗證 `key` query string。

    POST /messages/ 只帶 `session_id`、不帶 `key`（與 2.2 的 domain 綁定
    受同一個 transport 限制），但 `session_id` 是 128-bit UUID4，只有
    先通過本驗證、成功建立 SSE 連線才拿得到，所以只需要在「還沒有
    session_id」的請求上比對金鑰即可涵蓋整條連線的存取控制。
    """

    def __init__(self, app, expected_key: str | None) -> None:
        self._app = app
        self._expected_key = expected_key

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or get_query_param(scope, _SESSION_ID_QUERY_PARAM) is not None:
            await self._app(scope, receive, send)
            return
        if not self._is_authorized(get_query_param(scope, _KEY_QUERY_PARAM)):
            await _respond_unauthorized(send)
            return
        await self._app(scope, receive, send)

    def _is_authorized(self, provided_key: str | None) -> bool:
        if self._expected_key is None or provided_key is None:
            return False
        return hmac.compare_digest(provided_key, self._expected_key)


async def _respond_unauthorized(send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": _UNAUTHORIZED_STATUS,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": b"Unauthorized"})
