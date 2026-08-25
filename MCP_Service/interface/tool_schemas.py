from typing import Annotated

from pydantic import BaseModel, Field

SaveMemoryTitle = Annotated[str, Field(description="記憶主題。")]
SaveMemoryContext = Annotated[
    str,
    Field(
        max_length=500,
        description="限制 500 字內的摘要內容，建議內部區分 why 與 how_to_apply。",
    ),
]
SaveMemoryType = Annotated[str, Field(description="記憶類別（如 Notes, DailyReport, Preference）。")]
SaveMemoryTags = Annotated[
    list[str] | None,
    Field(
        default=None,
        description=(
            "(array of strings, optional) 關聯的標籤數組。如果記憶內容涉及特定的技術術語、錯誤代碼"
            "（如 'ERR_CORS'）、函式名（如 'get_user'）、股票代號（如 '0056'）等精確字串，"
            "務必將這些關鍵字也作為獨立的標籤存入此陣列，以利日後進行精確匹配檢索。"
        ),
    ),
]
SaveMemorySourceId = Annotated[
    str | None, Field(default=None, description="原始資料/對話關聯 ID。")
]
SaveMemoryImportanceScore = Annotated[
    int | None,
    Field(default=None, ge=1, le=10, description="AI 自評重要性，未提供則預設中間值。"),
]

SearchMemoriesQuery = Annotated[
    str, Field(description="查詢字句（例如：「上次討論 0056 的停利點設定是什麼？」）。")
]
SearchMemoriesType = Annotated[
    str | None, Field(default=None, description="指定記憶類別進行過濾。")
]
SearchMemoriesExactTags = Annotated[
    list[str] | None,
    Field(
        default=None,
        description=(
            "(array of strings, optional) 需要精確比對的關鍵字，用於觸發精確匹配軌道，"
            "彌補向量檢索對精確字串比對能力較弱的問題。當查詢中包含精確的技術字串"
            "（如錯誤代碼 'ERR_404'、函式名稱 'calculate_tax'、股票代號 '0056' 等）時，"
            "務必將這些關鍵字傳入此參數。"
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

LoadPinnedMemoriesLimit = Annotated[
    int, Field(default=5, ge=1, description="上限筆數，避免常駐清單膨脹造成 context 污染與 Token 成本增加。")
]


class MemoryView(BaseModel):
    doc_id: str
    type: str
    title: str
    context: str
    tags: list[str] = Field(default_factory=list)
    importance_score: int


class SaveMemoryResponse(BaseModel):
    decision: str
    doc_id: str | None


class SearchMemoriesResponse(BaseModel):
    memories: list[MemoryView]
