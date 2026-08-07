"""Independent Knowledge layer (Layer B) for Audio Lab.

Local recording assets (Layer A) and Knowledge documents must never cascade-delete.
"""

from app.knowledge.models import KnowledgeDocument, KnowledgeStatus, KnowledgeSourceType
from app.knowledge.registry import KnowledgeRegistry
from app.knowledge.service import KnowledgeError, KnowledgeService, knowledge_service

__all__ = [
    "KnowledgeDocument",
    "KnowledgeError",
    "KnowledgeRegistry",
    "KnowledgeService",
    "KnowledgeSourceType",
    "KnowledgeStatus",
    "knowledge_service",
]
