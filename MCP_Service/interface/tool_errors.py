import functools
import logging
import uuid

from mcp.server.mcpserver.exceptions import ToolError

from domain.exceptions import (
    DomainNotRegisteredError,
    MemoryNotFoundError,
    UpstreamComponent,
    UpstreamServiceError,
)

_logger = logging.getLogger(__name__)

_ERROR_CODE_LENGTH = 8
_PART_SEPARATOR = "｜"
_NOT_WRITTEN = "內容未寫入"
_WRITE_STATE_UNKNOWN = "寫入狀態不確定，請先查詢確認目前狀態"


def translate_tool_errors(*, read_only: bool):
    """MCP SDK 會把未捕捉的例外吞成只有 "Error executing tool X"，這裡統一轉成帶原因的 ToolError。"""

    def decorator(tool):
        @functools.wraps(tool)
        async def wrapper(*args, **kwargs):
            try:
                return await tool(*args, **kwargs)
            except ToolError:
                raise
            except (DomainNotRegisteredError, MemoryNotFoundError, ValueError) as error:
                raise ToolError(str(error)) from error
            except UpstreamServiceError as error:
                raise ToolError(_describe_upstream_failure(error, read_only)) from error
            except Exception as error:
                code = uuid.uuid4().hex[:_ERROR_CODE_LENGTH]
                _logger.exception("工具 %s 發生未預期錯誤（代碼 %s）", tool.__name__, code)
                raise ToolError(_describe_unexpected_failure(code, read_only)) from error

        return wrapper

    return decorator


def _describe_upstream_failure(error: UpstreamServiceError, read_only: bool) -> str:
    return _join_parts(str(error), _write_state(error.component, read_only), error.kind.advice)


def _describe_unexpected_failure(code: str, read_only: bool) -> str:
    return _join_parts(
        f"【內部錯誤】未預期的錯誤（代碼 {code}）",
        None if read_only else _WRITE_STATE_UNKNOWN,
        "請把代碼回報給維護者",
    )


def _write_state(component: UpstreamComponent, read_only: bool) -> str | None:
    if read_only:
        return None
    # embedding 與分類器都發生在任何寫入之前；只有記憶庫本身的失敗可能留下部分寫入。
    return _WRITE_STATE_UNKNOWN if component == UpstreamComponent.MEMORY_STORE else _NOT_WRITTEN


def _join_parts(*parts: str | None) -> str:
    return _PART_SEPARATOR.join(part for part in parts if part)
