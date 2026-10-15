import unittest

from mcp.server.mcpserver.exceptions import ToolError

from domain.exceptions import (
    DomainNotRegisteredError,
    MemoryNotFoundError,
    UpstreamComponent,
    UpstreamFailureKind,
    UpstreamServiceError,
)
from interface.tool_errors import translate_tool_errors


def _raising(error: Exception, *, read_only: bool):
    @translate_tool_errors(read_only=read_only)
    async def tool():
        raise error

    return tool


async def _message_of(error: Exception, *, read_only: bool) -> str:
    try:
        await _raising(error, read_only=read_only)()
    except ToolError as tool_error:
        return str(tool_error)
    raise AssertionError("預期要丟出 ToolError")


class TranslateToolErrorsTest(unittest.IsolatedAsyncioTestCase):
    async def test_classifier_failure_tells_caller_nothing_was_written_and_when_to_retry(self):
        # 呼叫端要靠這則訊息決定「能不能直接重送」：必須講明哪個環節、有沒有寫入、重試建議。
        error = UpstreamServiceError(UpstreamComponent.GATE_CLASSIFIER, UpstreamFailureKind.OVERLOADED, "HTTP 503")

        message = await _message_of(error, read_only=False)

        self.assertIn("寫入閘門（Gemini 分類器）", message)
        self.assertIn("服務暫時過載", message)
        self.assertIn("內容未寫入", message)
        self.assertIn("可稍後重試", message)

    async def test_firestore_failure_on_write_warns_state_is_uncertain(self):
        # Firestore 失敗可能發生在 UPDATE/SUPERSEDE 的多步驟寫入中途，不能宣稱「未寫入」。
        error = UpstreamServiceError(UpstreamComponent.MEMORY_STORE, UpstreamFailureKind.UNAVAILABLE)

        message = await _message_of(error, read_only=False)

        self.assertIn("寫入狀態不確定", message)
        self.assertNotIn("內容未寫入", message)

    async def test_read_only_tool_never_mentions_write_state(self):
        error = UpstreamServiceError(UpstreamComponent.EMBEDDING, UpstreamFailureKind.QUOTA_EXCEEDED, "HTTP 429")

        message = await _message_of(error, read_only=True)

        self.assertIn("Embedding（Gemini）", message)
        self.assertNotIn("寫入", message)

    async def test_non_retryable_failure_says_retrying_is_useless(self):
        error = UpstreamServiceError(UpstreamComponent.GATE_CLASSIFIER, UpstreamFailureKind.MODEL_UNAVAILABLE, "HTTP 404")

        message = await _message_of(error, read_only=False)

        self.assertIn("重試無效", message)

    async def test_business_errors_pass_through_with_their_own_message(self):
        domain_error = DomainNotRegisteredError(("dev", "finance"))
        not_found_error = MemoryNotFoundError("abc123")

        self.assertEqual(str(domain_error), await _message_of(domain_error, read_only=True))
        self.assertEqual(str(not_found_error), await _message_of(not_found_error, read_only=False))

    async def test_unexpected_error_gets_a_code_instead_of_the_generic_sdk_message(self):
        with self.assertLogs("interface.tool_errors", level="ERROR") as logs:
            message = await _message_of(RuntimeError("boom"), read_only=False)

        self.assertIn("內部錯誤", message)
        self.assertNotIn("boom", message)
        code = message.split("代碼 ")[1].split("）")[0]
        self.assertIn(code, logs.output[0])

    async def test_tool_error_raised_inside_tool_is_not_rewrapped(self):
        message = await _message_of(ToolError("premise 目前 600 字"), read_only=False)

        self.assertEqual("premise 目前 600 字", message)


if __name__ == "__main__":
    unittest.main()
