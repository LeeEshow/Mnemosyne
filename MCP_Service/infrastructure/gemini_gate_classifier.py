import json

from google import genai
from google.genai import types

import config
from domain.models import Memory
from domain.write_gate_policy import GateVerdict, WriteGateDecision

_PROMPT_TEMPLATE = """你是記憶庫寫入判定助手，請比較「新記憶」與「候選記憶」，判斷應採取下列何種動作：
NOOP：新記憶與候選記憶語意完全相同，不需寫入
UPDATE：新記憶是候選記憶的補充或修正，應合併兩者內容覆寫候選記憶（高相似度不代表語意重複，可能只是舊記憶的超集合，這種情況必須合併而非直接 NOOP，否則會遺失新增資訊）
SUPERSEDE：新記憶推翻候選記憶（候選記憶已過時或錯誤）
ADD：新記憶與候選記憶語意不同，應新增為獨立記錄

新記憶標題：{new_title}
新記憶內容：{new_context}

候選記憶標題：{candidate_title}
候選記憶內容：{candidate_context}

若判定為 UPDATE，merged_title/merged_context 必須是合併新舊記憶後的完整內容（限制 500 字內），而非僅新記憶或僅差異片段。"""

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": [item.value for item in WriteGateDecision]},
        "merged_title": {"type": "string"},
        "merged_context": {"type": "string"},
    },
    "required": ["decision"],
}


class GeminiGateClassifier:
    def __init__(self) -> None:
        self._client = genai.Client(
            vertexai=True,
            project=config.GOOGLE_CLOUD_PROJECT_ID,
            location=config.GOOGLE_CLOUD_LOCATION,
        )

    async def classify(self, new_memory: Memory, candidate: Memory) -> GateVerdict:
        prompt = self._build_prompt(new_memory, candidate)
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
            merged_title=payload.get("merged_title"),
            merged_context=payload.get("merged_context"),
        )

    def _build_prompt(self, new_memory: Memory, candidate: Memory) -> str:
        return _PROMPT_TEMPLATE.format(
            new_title=new_memory.title,
            new_context=new_memory.context,
            candidate_title=candidate.title,
            candidate_context=candidate.context,
        )
