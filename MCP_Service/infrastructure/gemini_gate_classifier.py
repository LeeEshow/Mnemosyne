import json
import logging
import os

from google import genai
from google.genai import types

import config
from domain.exceptions import UpstreamComponent, UpstreamFailureKind, UpstreamServiceError
from domain.models import Memory
from domain.write_gate_policy import GateVerdict, WriteGateDecision
from infrastructure.upstream_errors import GenaiFailure, translate_genai_error

_logger = logging.getLogger(__name__)

# 可用另一個模型繞過的失敗；認證、請求格式、回傳格式問題換模型也不會好。
_FALLBACK_ELIGIBLE_KINDS = frozenset(
    {
        UpstreamFailureKind.OVERLOADED,
        UpstreamFailureKind.QUOTA_EXCEEDED,
        UpstreamFailureKind.UNAVAILABLE,
        UpstreamFailureKind.MODEL_UNAVAILABLE,
    }
)

_PROMPT_TEMPLATE = """你是記憶庫寫入判定助手，請比較「新記憶」與「候選記憶清單」，選擇動作：
1. NOOP：語意完全相同。
2. UPDATE（相容增補）：新舊資訊邏輯不衝突，可合併並存。
   - 範例：舊「偏好 Python」+ 新「網頁開發偏好用 Go」→ 合併。
   - 範例：舊「愛吃牛肉麵」+ 新「吃麵偏好粗麵」→ 合併。
3. SUPERSEDE（純技術/聯絡類中繼資料的無爭議更迭）：僅限電話、地址、Email、軟體版本號、序號這類**低風險、沒有解讀空間的欄位**被新值取代。
   - 範例：舊「電話是 A」+ 新「電話改為 B」→ SUPERSEDE。
   - ⚠️凡是涉及「人員/職務/責任歸屬」、「使用者的偏好、能力、允許狀態」的變更，即使表面上也是「單一值被新值取代」，一律不可判為 SUPERSEDE，必須判為 CONFLICT_DETECTED——這兩種欄位一旦誤判自動覆蓋，風險遠高於聯絡資訊打錯字。
4. CONFLICT_DETECTED（邏輯矛盾/偏好或人員歸屬對立）：新記憶否定、禁止或推翻了舊記憶陳述的偏好、能力、允許狀態，或人員/職務/責任歸屬。
   - 範例：舊「偏好線上會議」+ 新「討厭線上會議」→ CONFLICT_DETECTED。
   - 範例：舊「這個專案的 PM 是 Alice」+ 新「Bob 已接任成為新 PM」→ CONFLICT_DETECTED（人員歸屬變更，不可視同聯絡資訊更新）。
   - 範例：舊「愛吃牛肉麵」+ 新「宗教因素完全禁食牛肉」→ CONFLICT_DETECTED（即使新記憶本身講得像是在「更新」舊資訊，只要否定了先前的偏好/能力陳述，就不可自動覆蓋）。
5. ADD：與所有候選記憶語意皆不同且無關，應新增為獨立記錄。

新記憶標題：{new_title}
新記憶因（premise）：{new_premise}
新記憶果（conclusion）：{new_conclusion}

候選記憶清單：
{candidates_block}

若判定為 NOOP/UPDATE/SUPERSEDE/CONFLICT_DETECTED，matched_memory_id 必須是上方候選記憶清單中對應的 id，不可留空或虛構不存在的 id；若判定為 ADD，matched_memory_id 留空。
若判定為 UPDATE 或 SUPERSEDE，merged_title/merged_premise/merged_conclusion 必須是合併新舊記憶後的完整內容（各限制 500 字內），而非僅新記憶或僅差異片段；SUPERSEDE 須以「重新摘要」而非「逐字串接」的方式，整合舊結論、新資訊、修正後結論成一段簡潔敘事。"""

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": [item.value for item in WriteGateDecision]},
        "matched_memory_id": {"type": "string"},
        "merged_title": {"type": "string"},
        "merged_premise": {"type": "string"},
        "merged_conclusion": {"type": "string"},
    },
    "required": ["decision"],
}


class GeminiGateClassifier:
    def __init__(self) -> None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if api_key:
            # 個人 Google AI Studio API Key，走計量計費以外的個人訂閱額度。
            self._client = genai.Client(api_key=api_key)
        else:
            self._client = genai.Client(
                vertexai=True,
                project=config.GOOGLE_CLOUD_PROJECT_ID,
                location=config.GEMINI_CLASSIFIER_LOCATION,
            )

    async def classify(self, new_memory: Memory, candidates: tuple[Memory, ...]) -> GateVerdict:
        prompt = self._build_prompt(new_memory, candidates)
        response = await self._generate_with_fallback(prompt)
        return self._parse_verdict(response)

    async def _generate_with_fallback(self, prompt: str):
        primary_model = config.GATE_CLASSIFIER_MODEL
        fallback_model = config.GATE_CLASSIFIER_FALLBACK_MODEL
        try:
            return await self._generate(primary_model, prompt)
        except UpstreamServiceError as primary_error:
            if fallback_model is None or primary_error.kind not in _FALLBACK_ELIGIBLE_KINDS:
                raise
            _logger.warning("分類器主模型 %s 失敗（%s），改用備援模型 %s", primary_model, primary_error, fallback_model)
            try:
                return await self._generate(fallback_model, prompt)
            except UpstreamServiceError as fallback_error:
                detail = f"主模型 {primary_model} 與備援模型 {fallback_model} 皆失敗"
                raise UpstreamServiceError(UpstreamComponent.GATE_CLASSIFIER, fallback_error.kind, detail) from fallback_error

    async def _generate(self, model: str, prompt: str):
        try:
            return await self._client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_schema=_RESPONSE_SCHEMA
                ),
            )
        except GenaiFailure as error:
            raise translate_genai_error(UpstreamComponent.GATE_CLASSIFIER, error) from error

    def _parse_verdict(self, response) -> GateVerdict:
        try:
            payload = json.loads(response.text)
            return GateVerdict(
                decision=WriteGateDecision(payload["decision"]),
                matched_memory_id=payload.get("matched_memory_id") or None,
                merged_title=payload.get("merged_title"),
                merged_premise=payload.get("merged_premise"),
                merged_conclusion=payload.get("merged_conclusion"),
            )
        except (TypeError, ValueError, KeyError, AttributeError) as error:
            raise UpstreamServiceError(
                UpstreamComponent.GATE_CLASSIFIER, UpstreamFailureKind.BAD_RESPONSE, type(error).__name__
            ) from error

    def _build_prompt(self, new_memory: Memory, candidates: tuple[Memory, ...]) -> str:
        candidates_block = "\n".join(self._render_candidate(candidate) for candidate in candidates)
        return _PROMPT_TEMPLATE.format(
            new_title=new_memory.title,
            new_premise=new_memory.premise,
            new_conclusion=new_memory.conclusion,
            candidates_block=candidates_block,
        )

    def _render_candidate(self, candidate: Memory) -> str:
        return (
            f"- id: {candidate.id}\n"
            f"  標題：{candidate.title}\n"
            f"  因（premise）：{candidate.premise}\n"
            f"  果（conclusion）：{candidate.conclusion}"
        )
