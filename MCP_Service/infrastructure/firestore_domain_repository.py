import asyncio

from firebase_admin import firestore
from google.cloud.firestore_v1.document import DocumentSnapshot

import config
from domain.models import Domain
from infrastructure import firebase_app


class FirestoreDomainRepository:
    def __init__(self) -> None:
        firebase_app.ensure_initialized()
        self._collection = firestore.client().collection(config.DOMAINS_COLLECTION_NAME)

    async def find_by_name(self, name: str) -> Domain | None:
        snapshot = await asyncio.to_thread(self._collection.document(name).get)
        return self._to_domain(snapshot) if snapshot.exists else None

    async def list_all(self) -> list[Domain]:
        snapshots = await asyncio.to_thread(lambda: list(self._collection.get()))
        return [self._to_domain(snapshot) for snapshot in snapshots]

    async def save(self, domain: Domain) -> Domain:
        await asyncio.to_thread(self._collection.document(domain.name).set, self._to_document(domain))
        return domain

    def _to_document(self, domain: Domain) -> dict:
        return {"name": domain.name, "description": domain.description, "created_at": domain.created_at}

    def _to_domain(self, snapshot: DocumentSnapshot) -> Domain:
        data = snapshot.to_dict()
        return Domain(name=data["name"], description=data["description"], created_at=data["created_at"])
