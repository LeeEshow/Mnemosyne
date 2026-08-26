"""部署前置步驟（阻塞性，見 Task.md 2.2、Proposal 2.3 第 6 點）。

上線 Domain Registry 攔截前必須先執行一次：掃描既有 `memories` 集合中所有 distinct 的
`domain` 值，批次寫入 `domains` 完成 seed，避免舊資料在切換當下被誤判為未註冊而擋下存取。
`"global"` 一併明確 seed，不依賴掃描結果自動涵蓋。可重複執行，已註冊的 domain 會直接跳過。

執行方式：於 MCP_Service/ 目錄下 `python -m scripts.seed_domain_registry`。
"""

import asyncio
from datetime import datetime, timezone

from firebase_admin import firestore

import config
from domain.domain_naming import normalize_domain_name
from domain.models import Domain
from infrastructure import firebase_app
from infrastructure.firestore_domain_repository import FirestoreDomainRepository

_GLOBAL_DOMAIN_DESCRIPTION = "全域通用偏好與設定，檢索時會自動與指定領域合併，請勿在此寫入特定技術或專案知識。"
_MIGRATED_DOMAIN_DESCRIPTION = "（既有資料遷移自動產生，尚未填寫定位描述，請視需要更新。）"


def _scan_existing_domain_names() -> set[str]:
    firebase_app.ensure_initialized()
    collection = firestore.client().collection(config.MEMORIES_COLLECTION_NAME)
    snapshots = collection.select(["domain"]).get()
    return {normalize_domain_name(snapshot.get("domain")) for snapshot in snapshots}


async def _seed(names: set[str], repository: FirestoreDomainRepository) -> None:
    now = datetime.now(timezone.utc)
    for name in sorted(names | {config.GLOBAL_DOMAIN}):
        if await repository.find_by_name(name) is not None:
            print(f"skip（已註冊）：{name}")
            continue
        description = _GLOBAL_DOMAIN_DESCRIPTION if name == config.GLOBAL_DOMAIN else _MIGRATED_DOMAIN_DESCRIPTION
        await repository.save(Domain(name=name, description=description, created_at=now))
        print(f"seed 完成：{name}")


async def main() -> None:
    names = _scan_existing_domain_names()
    await _seed(names, FirestoreDomainRepository())


if __name__ == "__main__":
    asyncio.run(main())
