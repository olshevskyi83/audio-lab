from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.knowledge.models import (
    KnowledgeDocument,
    KnowledgeSourceType,
    KnowledgeStatus,
    utc_now_iso,
)
from app.knowledge.registry import KnowledgeRegistry


class KnowledgeError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _default_registry_path() -> Path:
    audio_root = Path(os.environ.get("AUDIO_ROOT", "/remote/Audio")).resolve()
    override = os.environ.get("KNOWLEDGE_REGISTRY_PATH")
    if override:
        return Path(override).resolve()
    return audio_root / "knowledge" / "registry.json"


class KnowledgeService:
    """Layer B: Knowledge documents independent from local recording assets."""

    def __init__(self, registry: KnowledgeRegistry | None = None) -> None:
        self.registry = registry or KnowledgeRegistry(_default_registry_path())

    def ensure_ready(self) -> None:
        self.registry.ensure_ready()

    def list_documents(self) -> list[dict[str, Any]]:
        return [doc.to_public_dict() for doc in self.registry.list_documents()]

    def get(self, knowledge_id: str) -> dict[str, Any]:
        doc = self.registry.get(knowledge_id)
        if doc is None:
            raise KnowledgeError("Knowledge document not found", status_code=404)
        return doc.to_public_dict()

    def state_for_source(
        self,
        source_type: KnowledgeSourceType | str,
        source_ref: str,
        *,
        allow_add: bool = True,
    ) -> dict[str, Any]:
        doc = self.registry.get_by_source(source_type, source_ref)
        if doc and doc.status == KnowledgeStatus.INDEXED:
            return {
                "status": "indexed",
                "available": True,
                "indexed": True,
                "knowledge_id": doc.knowledge_id,
                "document_id": doc.document_id,
                "message": "База знань: Додано ✓",
                "can_add": False,
                "can_reindex": True,
                "can_remove": True,
                "local_assets_present": doc.local_assets_present,
                "vector_backend": doc.vector_backend,
            }
        return {
            "status": "not_indexed",
            "available": allow_add,
            "indexed": False,
            "knowledge_id": None,
            "document_id": None,
            "message": "Не в базі знань",
            "can_add": allow_add,
            "can_reindex": False,
            "can_remove": False,
            "local_assets_present": True,
            "vector_backend": None,
        }

    def add_for_live_recording(
        self,
        *,
        session_id: str,
        title: str,
    ) -> dict[str, Any]:
        existing = self.registry.get_by_source(
            KnowledgeSourceType.LIVE_RECORDING,
            session_id,
        )
        if existing and existing.status == KnowledgeStatus.INDEXED:
            return existing.to_public_dict()

        knowledge_id = existing.knowledge_id if existing else None
        doc = KnowledgeDocument.create(
            title=title,
            source_type=KnowledgeSourceType.LIVE_RECORDING,
            source_ref=session_id,
            vector_backend="deferred",
            knowledge_id=knowledge_id,
            local_assets_present=True,
            metadata={"recording_id": session_id},
        )
        return self.registry.upsert(doc).to_public_dict()

    def reindex_for_live_recording(
        self,
        *,
        session_id: str,
        title: str,
    ) -> dict[str, Any]:
        existing = self.registry.get_by_source(
            KnowledgeSourceType.LIVE_RECORDING,
            session_id,
        )
        if existing is None or existing.status != KnowledgeStatus.INDEXED:
            raise KnowledgeError(
                "Recording is not in the Knowledge Base",
                status_code=409,
            )
        existing.title = title or existing.title
        existing.updated_at = utc_now_iso()
        existing.local_assets_present = True
        existing.metadata["recording_id"] = session_id
        return self.registry.upsert(existing).to_public_dict()

    def register_upload_task(
        self,
        *,
        task_id: str,
        title: str,
        source_file: str | None = None,
        chunk_count: int | None = None,
    ) -> dict[str, Any]:
        existing = self.registry.get_by_source(
            KnowledgeSourceType.UPLOAD_TASK,
            task_id,
        )
        # Homelab Core task id is the stable generic Knowledge document id.
        knowledge_id = existing.knowledge_id if existing else task_id
        metadata: dict[str, Any] = {"task_id": task_id}
        if source_file:
            metadata["source_file"] = Path(source_file).name
            metadata["archive_file"] = Path(source_file).name
        if chunk_count is not None:
            metadata["chunk_count"] = chunk_count

        doc = KnowledgeDocument.create(
            title=title,
            source_type=KnowledgeSourceType.UPLOAD_TASK,
            source_ref=task_id,
            vector_backend="core",
            knowledge_id=knowledge_id,
            local_assets_present=True,
            metadata=metadata,
        )
        if existing:
            doc.created_at = existing.created_at
        return self.registry.upsert(doc).to_public_dict()

    def remove(self, knowledge_id: str) -> dict[str, Any]:
        """Remove from Knowledge only — local audio/transcripts remain."""
        doc = self.registry.delete(knowledge_id)
        if doc is None:
            raise KnowledgeError("Knowledge document not found", status_code=404)
        return {
            "knowledge_id": knowledge_id,
            "document_id": doc.document_id,
            "removed": True,
            "local_assets_preserved": True,
        }

    def remove_by_source(
        self,
        source_type: KnowledgeSourceType | str,
        source_ref: str,
    ) -> dict[str, Any] | None:
        doc = self.registry.get_by_source(source_type, source_ref)
        if doc is None:
            return None
        return self.remove(doc.knowledge_id)

    def mark_local_deleted(
        self,
        source_type: KnowledgeSourceType | str,
        source_ref: str,
    ) -> dict[str, Any] | None:
        """Local assets gone; Knowledge document remains manageable."""
        doc = self.registry.get_by_source(source_type, source_ref)
        if doc is None:
            return None
        doc.local_assets_present = False
        doc.updated_at = utc_now_iso()
        doc.metadata["local_deleted_at"] = doc.updated_at
        return self.registry.upsert(doc).to_public_dict()

    def is_filename_indexed(self, filename: str) -> dict[str, Any] | None:
        doc = self.registry.find_by_local_filename(filename)
        if doc is None or doc.status != KnowledgeStatus.INDEXED:
            return None
        return doc.to_public_dict()


knowledge_service = KnowledgeService()
