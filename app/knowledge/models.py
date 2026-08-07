from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeStatus(StrEnum):
    INDEXED = "indexed"
    NOT_INDEXED = "not_indexed"


class KnowledgeSourceType(StrEnum):
    LIVE_RECORDING = "live_recording"
    UPLOAD_TASK = "upload_task"
    EXTERNAL = "external"


class KnowledgeDocument(BaseModel):
    """Stable Knowledge object independent of local filesystem paths."""

    knowledge_id: str
    document_id: str
    title: str
    source_type: KnowledgeSourceType
    source_ref: str | None = None
    status: KnowledgeStatus = KnowledgeStatus.INDEXED
    local_assets_present: bool = True
    vector_backend: str = "deferred"
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        title: str,
        source_type: KnowledgeSourceType,
        source_ref: str | None = None,
        vector_backend: str = "deferred",
        metadata: dict[str, Any] | None = None,
        knowledge_id: str | None = None,
        local_assets_present: bool = True,
    ) -> KnowledgeDocument:
        now = utc_now_iso()
        kid = knowledge_id or str(uuid4())
        return cls(
            knowledge_id=kid,
            document_id=kid,
            title=(title or "").strip() or "Untitled recording",
            source_type=source_type,
            source_ref=source_ref,
            status=KnowledgeStatus.INDEXED,
            local_assets_present=local_assets_present,
            vector_backend=vector_backend,
            created_at=now,
            updated_at=now,
            metadata=dict(metadata or {}),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "knowledge_id": self.knowledge_id,
            "document_id": self.document_id,
            "title": self.title,
            "source_type": self.source_type.value,
            "source_ref": self.source_ref,
            "status": self.status.value,
            "indexed": self.status == KnowledgeStatus.INDEXED,
            "local_assets_present": self.local_assets_present,
            "vector_backend": self.vector_backend,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "manageable_without_local": True,
        }
