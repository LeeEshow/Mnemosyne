from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field

SaveMemoryDomain = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "領域分區（如 'coding'/'finance'/'life'），須為已註冊的 domain（正規化後比對，大小寫與前後"
            "空白不敏感）。請優先參考本說明後段動態列出的既有 domain 清單選用，不要憑空生成新名稱；"
            "填入未註冊的值會被拒絕，回傳 decision=\"requires_registration\" 並附上已註冊清單。"
            "⚠️ 硬性規則：收到 requires_registration 後，你必須先在對話中向使用者說明這是尚未存在的新領域、"
            "其定位為何，取得明確同意後才可呼叫 register_domain 完成註冊，不可自行判斷觸發，"
            "也不可用既有 domain 隨意頂替。"
        ),
    ),
]
SaveMemoryTitle = Annotated[str, Field(description="記憶主題，用於人工瀏覽時快速辨識（例如：「0056 停利點設定」）。")]
SaveMemoryPremise = Annotated[
    str,
    Field(
        max_length=500,
        description=(
            "因——限制 500 字內的精煉脈絡，促成這筆記憶的情境或前提"
            "（例如：「使用者詢問 0056 的停利點設定邏輯，過去曾因停利點設太低而錯過後續漲幅」）。"
            "若這是任務完成後的回顧，此欄位填任務過程與成因。"
        ),
    ),
]
SaveMemoryConclusion = Annotated[
    str,
    Field(
        max_length=500,
        description=(
            "果——決策/結論，實際會被套用的行為依據"
            "（例如：「0056 的停利點固定採用 20% 移動停利，不再使用固定金額停利」）。"
            "若這是任務完成後的回顧，此欄位填經驗教訓，不需要另外呼叫別的工具。"
        ),
    ),
]
SaveMemoryType = Annotated[str, Field(description="記憶類別（如 'Notes'、'DailyReport'、'Preference'）。")]
SaveMemoryTags = Annotated[
    list[str] | None,
    Field(
        default=None,
        description=(
            "(array of strings, optional) 關聯的標籤數組。除了精確技術字串（錯誤代碼如 'ERR_CORS'、"
            "函式名如 'get_user'、股票代號如 '0056'）外，也務必提取記憶內容中的**核心主題實體**"
            "（人名、食物、具體事物、關鍵概念，例如「媽媽」「牛肉麵」「停損策略」）——不要只挑技術字串，"
            "這是系統能否偵測到「低相似度但主題相關」的修正型衝突（例如喜好類陳述的修正）的前提。"
        ),
    ),
]
SaveMemoryImportanceScore = Annotated[
    int | None,
    Field(default=None, ge=1, le=10, description="AI 自評重要性（1-10），未提供則預設中間值。"),
]

SearchMemoriesDomain = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "領域分區，須為已註冊的 domain（正規化後比對）。選用方式與未註冊時的硬性規則同 save_memory 的"
            "domain 參數說明，差異僅在於：這裡填入未註冊的值會直接拋出錯誤讓本次呼叫失敗，不會回傳空結果"
            "（空結果可能被誤判為「使用者從未提過」而產生錯誤斷言）。"
        ),
    ),
]
SearchMemoriesQuery = Annotated[
    str, Field(description="查詢字句（例如：「上次討論 0056 的停利點設定是什麼？」）。")
]
SearchMemoriesType = Annotated[
    str | None, Field(default=None, description="指定記憶類別進行過濾（如 'Notes'、'Preference'）。")
]
SearchMemoriesExactTags = Annotated[
    list[str] | None,
    Field(
        default=None,
        description=(
            "(array of strings, optional) 需要精確比對的關鍵字，用於觸發精確匹配軌道，"
            "彌補向量檢索對精確字串比對能力較弱的問題。當查詢中包含精確的技術字串"
            "（如錯誤代碼 'ERR_404'、函式名稱 'calculate_tax'、股票代號 '0056'）或明確的主題實體"
            "（如人名、食物等具體事物）時，務必將這些關鍵字傳入此參數。"
        ),
    ),
]
SearchMemoriesLimit = Annotated[
    int, Field(default=3, ge=1, description="返回的最大記憶數量。")
]
SearchMemoriesIncludeSuperseded = Annotated[
    bool, Field(default=False, description="是否納入已被取代（superseded）的記憶。")
]
SearchMemoriesIncludeArchived = Annotated[
    bool, Field(default=False, description="是否納入已封存（archived）的記憶。")
]

ForgetMemoryDocId = Annotated[str, Field(description="Firestore 文件 ID。")]
ForgetMemoryHardDelete = Annotated[
    bool,
    Field(default=False, description="為 true 時才真正刪除文件；預設僅將 status 設為 archived。"),
]

PinMemoryDocId = Annotated[str, Field(description="Firestore 文件 ID。")]

LoadPinnedMemoriesDomain = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "領域分區，須為已註冊的 domain，未註冊會導致本次呼叫失敗（拋出錯誤），"
            "處理方式（含 register_domain 硬性規則）同 search_memories 的 domain 參數說明。"
        ),
    ),
]
LoadPinnedMemoriesLimit = Annotated[
    int, Field(default=5, ge=1, description="上限筆數，避免常駐清單膨脹造成 context 污染與 Token 成本增加。")
]

RegisterDomainName = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "新 domain 名稱（如 'coding'/'finance'/'life'），寫入前會正規化（去除前後空白、轉小寫）"
            "並檢查唯一性，重複則回傳既有註冊資訊、不會重複建立。"
        ),
    ),
]
RegisterDomainDescription = Annotated[
    str,
    Field(
        description=(
            "該領域的定位與語意邊界描述，供日後動態注入到各工具 domain 參數說明、協助判斷是否該沿用此 domain"
            "（例如：「跨專案的程式開發知識，包含除錯經驗、架構決策、常用函式庫用法」）。"
        ),
    ),
]


class MemoryView(BaseModel):
    doc_id: str
    type: str
    title: str
    premise: str
    conclusion: str
    tags: list[str] = Field(default_factory=list)
    importance_score: int


class SaveMemoryResponse(BaseModel):
    decision: str
    doc_id: str | None
    registered_domains: list[str] | None = None
    conflicting_memory: MemoryView | None = None


class SearchMemoriesResponse(BaseModel):
    memories: list[MemoryView]


class DomainView(BaseModel):
    name: str
    description: str
    created_at: datetime


class ListDomainsResponse(BaseModel):
    domains: list[DomainView]


class RegisterDomainResponse(BaseModel):
    domain: DomainView
    already_registered: bool
