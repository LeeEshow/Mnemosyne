from enum import Enum


class ConfigurationError(Exception):
    pass


class DomainNotRegisteredError(Exception):
    def __init__(self, registered_domains: tuple[str, ...]) -> None:
        self.registered_domains = registered_domains
        super().__init__(_build_message(registered_domains))


def _build_message(registered_domains: tuple[str, ...]) -> str:
    if not registered_domains:
        return "此 domain 尚未註冊，且目前沒有任何已註冊的 domain。"
    joined = "、".join(registered_domains)
    return f"此 domain 尚未註冊。已註冊的 domain：{joined}"


class MemoryNotFoundError(Exception):
    def __init__(self, memory_id: str) -> None:
        self.memory_id = memory_id
        super().__init__(f"找不到 doc_id={memory_id} 的記憶，請先用 search_memories 取得正確的 doc_id")


class UpstreamComponent(Enum):
    EMBEDDING = "Embedding（Gemini）"
    GATE_CLASSIFIER = "寫入閘門（Gemini 分類器）"
    MEMORY_STORE = "記憶庫（Firestore）"


class UpstreamFailureKind(Enum):
    OVERLOADED = ("服務暫時過載", "可稍後重試")
    QUOTA_EXCEEDED = ("API 配額暫時用盡", "約 1 分鐘後可重試")
    UNAVAILABLE = ("服務暫時無法連線或逾時", "可稍後重試")
    MODEL_UNAVAILABLE = ("指定的模型已不可用", "重試無效，請通知維護者")
    AUTHENTICATION = ("憑證或權限不足", "重試無效，請通知維護者")
    CONFIGURATION = ("請求或伺服器設定異常（例如缺少索引）", "重試無效，請通知維護者")
    BAD_RESPONSE = ("回傳內容格式異常", "可重試一次，持續發生請通知維護者")

    def __init__(self, reason: str, advice: str) -> None:
        self.reason = reason
        self.advice = advice

    @property
    def retryable(self) -> bool:
        return self in _RETRYABLE_KINDS


_RETRYABLE_KINDS = frozenset(
    {
        UpstreamFailureKind.OVERLOADED,
        UpstreamFailureKind.QUOTA_EXCEEDED,
        UpstreamFailureKind.UNAVAILABLE,
        UpstreamFailureKind.BAD_RESPONSE,
    }
)


class UpstreamServiceError(Exception):
    def __init__(self, component: UpstreamComponent, kind: UpstreamFailureKind, detail: str | None = None) -> None:
        self.component = component
        self.kind = kind
        self.detail = detail
        headline = f"【{component.value}】{kind.reason}"
        super().__init__(f"{headline}（{detail}）" if detail else headline)
