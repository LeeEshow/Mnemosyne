from typing import Protocol

from domain.models import Memory
from domain.write_gate_policy import GateVerdict


class GateClassifier(Protocol):
    async def classify(self, new_memory: Memory, candidates: tuple[Memory, ...]) -> GateVerdict: ...
