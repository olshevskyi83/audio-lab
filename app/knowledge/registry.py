from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app.knowledge.models import KnowledgeDocument, KnowledgeSourceType


class KnowledgeRegistry:
    """Filesystem-backed Knowledge document store (Layer B)."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def ensure_ready(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.is_file():
            self._write({"documents": {}})

    def list_documents(self) -> list[KnowledgeDocument]:
        self.ensure_ready()
        payload = self._read()
        docs = [
            KnowledgeDocument.model_validate(item)
            for item in payload.get("documents", {}).values()
        ]
        docs.sort(key=lambda item: item.updated_at, reverse=True)
        return docs

    def get(self, knowledge_id: str) -> KnowledgeDocument | None:
        self.ensure_ready()
        raw = self._read().get("documents", {}).get(knowledge_id)
        if not raw:
            return None
        return KnowledgeDocument.model_validate(raw)

    def get_by_source(
        self,
        source_type: KnowledgeSourceType | str,
        source_ref: str,
    ) -> KnowledgeDocument | None:
        wanted = (
            source_type
            if isinstance(source_type, KnowledgeSourceType)
            else KnowledgeSourceType(source_type)
        )
        for doc in self.list_documents():
            if doc.source_type == wanted and doc.source_ref == source_ref:
                return doc
        return None

    def find_by_local_filename(self, filename: str) -> KnowledgeDocument | None:
        safe = Path(filename).name
        for doc in self.list_documents():
            meta = doc.metadata or {}
            candidates = [
                meta.get("source_file"),
                meta.get("filename"),
                meta.get("archive_file"),
            ]
            if safe in {Path(str(c)).name for c in candidates if c}:
                return doc
        return None

    def upsert(self, document: KnowledgeDocument) -> KnowledgeDocument:
        self.ensure_ready()
        payload = self._read()
        documents = payload.setdefault("documents", {})
        documents[document.knowledge_id] = document.model_dump(mode="json")
        self._write(payload)
        return document

    def delete(self, knowledge_id: str) -> KnowledgeDocument | None:
        """Remove Knowledge registration only — never touches local assets."""
        self.ensure_ready()
        payload = self._read()
        documents = payload.get("documents", {})
        raw = documents.pop(knowledge_id, None)
        if raw is None:
            return None
        self._write(payload)
        return KnowledgeDocument.model_validate(raw)

    def _read(self) -> dict:
        if not self.path.is_file():
            return {"documents": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
