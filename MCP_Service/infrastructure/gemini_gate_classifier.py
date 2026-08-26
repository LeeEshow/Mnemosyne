import json

from google import genai
from google.genai import types

import config
from domain.models import Memory
from domain.write_gate_policy import GateVerdict, WriteGateDecision

_PROMPT_TEMPLATE = """你是記憶庫寫入判定助手，請比較「新記憶」與下列「候選記憶清單」，判斷應採取下列何種動作：
NOOP：新記憶與清單中某一筆候選記憶語意完全相同，不需寫入
UPDATE：新記憶是清單中某一筆候選記憶的補充或修正，應合併兩者內容覆寫該候選記憶（高相似度不代表語意重複，可能只是舊記憶的超集合，這種情況必須合併而非直接 NOOP，否則會遺失新增資訊）
SUPERSEDE：新記憶推翻清單中某一筆候選記憶（該候選記憶已過時或錯誤）
CONFLICT_DETECTED：新記憶與清單中某一筆候選記憶存在邏輯矛盾（而非單純的延伸或無關）
ADD：新記憶與清單中所有候選記憶語意皆不同，應新增為獨立記錄

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
        self._client = genai.Client(
            vertexai=True,
            project=config.GOOGLE_CLOUD_PROJECT_ID,
            location=config.GEMINI_CLASSIFIER_LOCATION,
        )

    async def classify(self, new_memory: Memory, candidates: tuple[Memory, ...]) -> GateVerdict:
        prompt = self._build_prompt(new_memory, candidates)
        response = await self._client.aio.models.generate_content(
            model=config.GATE_CLASSIFIER_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=_RESPONSE_SCHEMA
            ),
        )
        payload = json.loads(response.text)
        return GateVerdict(
            decision=WriteGateDecision(payload["decision"]),
            matched_memory_id=payload.get("matched_memory_id") or None,
            merged_title=payload.get("merged_title"),
            merged_premise=payload.get("merged_premise"),
            merged_conclusion=payload.get("merged_conclusion"),
        )

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
