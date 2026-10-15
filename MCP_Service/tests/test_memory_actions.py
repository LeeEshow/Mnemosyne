import unittest
from dataclasses import dataclass, field

from application.forget_memory_use_case import ForgetMemoryRequest, ForgetMemoryUseCase
from application.pin_memory_use_case import PinMemoryUseCase
from application.superseded_resolution import resolve_active_memory_id
from domain.exceptions import MemoryNotFoundError
from domain.models import MemoryStatus


@dataclass
class _StoredMemory:
    id: str
    status: MemoryStatus = MemoryStatus.ACTIVE
    superseded_by: str | None = None


@dataclass
class _FakeRepository:
    memories: dict[str, _StoredMemory]
    calls: list[tuple] = field(default_factory=list)

    async def get_by_id(self, memory_id):
        return self.memories.get(memory_id)

    async def delete(self, memory_id):
        self.calls.append(("delete", memory_id))

    async def set_status(self, memory_id, status):
        self.calls.append(("set_status", memory_id, status))

    async def pin(self, memory_id):
        self.calls.append(("pin", memory_id))

    async def unpin(self, memory_id):
        self.calls.append(("unpin", memory_id))


def _repository_with_chain() -> _FakeRepository:
    return _FakeRepository(
        {
            "A": _StoredMemory("A", MemoryStatus.SUPERSEDED, "B"),
            "B": _StoredMemory("B", MemoryStatus.SUPERSEDED, "C"),
            "C": _StoredMemory("C"),
        }
    )


class MemoryActionsTest(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_doc_id_is_reported_instead_of_silently_succeeding(self):
        # Firestore 對不存在文件的 delete 是冪等的；沒有先檢查，hard_delete 會對打錯的 id 假成功。
        repository = _FakeRepository({})

        with self.assertRaises(MemoryNotFoundError):
            await ForgetMemoryUseCase(repository).execute(ForgetMemoryRequest("typo", hard_delete=True))

        self.assertEqual([], repository.calls)

    async def test_forget_returns_the_active_id_that_was_actually_acted_on(self):
        # 呼叫端拿著舊 id 時，必須能從回傳值得知實際被封存的是哪一筆。
        repository = _repository_with_chain()

        acted_on = await ForgetMemoryUseCase(repository).execute(ForgetMemoryRequest("A"))

        self.assertEqual("C", acted_on)
        self.assertEqual([("set_status", "C", MemoryStatus.ARCHIVED)], repository.calls)

    async def test_pin_and_unpin_return_the_active_id(self):
        repository = _repository_with_chain()
        use_case = PinMemoryUseCase(repository)

        self.assertEqual("C", await use_case.execute("A", pinned=True))
        self.assertEqual("C", await use_case.execute("B", pinned=False))
        self.assertEqual([("pin", "C"), ("unpin", "C")], repository.calls)

    async def test_pin_unknown_doc_id_raises_not_found(self):
        with self.assertRaises(MemoryNotFoundError) as context:
            await PinMemoryUseCase(_FakeRepository({})).execute("typo")

        self.assertEqual("typo", context.exception.memory_id)

    async def test_cyclic_supersede_chain_terminates(self):
        repository = _FakeRepository(
            {
                "A": _StoredMemory("A", MemoryStatus.SUPERSEDED, "B"),
                "B": _StoredMemory("B", MemoryStatus.SUPERSEDED, "A"),
            }
        )

        await resolve_active_memory_id(repository, "A")


if __name__ == "__main__":
    unittest.main()
