from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.knowledge.service import KnowledgeError, knowledge_service


router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def _http_error(exc: KnowledgeError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/documents")
async def list_knowledge_documents() -> dict[str, Any]:
    """List Knowledge objects, including knowledge-only (no local assets)."""
    documents = knowledge_service.list_documents()
    return {"documents": documents, "total": len(documents)}


@router.get("/documents/{knowledge_id}")
async def get_knowledge_document(knowledge_id: str) -> dict[str, Any]:
    try:
        return knowledge_service.get(knowledge_id)
    except KnowledgeError as exc:
        raise _http_error(exc) from exc


@router.delete("/documents/{knowledge_id}")
async def remove_from_knowledge(knowledge_id: str) -> dict[str, Any]:
    """Remove from Knowledge only. Never deletes local recording assets."""
    try:
        return knowledge_service.remove(knowledge_id)
    except KnowledgeError as exc:
        raise _http_error(exc) from exc


@router.post("/documents/{knowledge_id}/remove-form")
async def remove_from_knowledge_form(knowledge_id: str) -> RedirectResponse:
    """HTML form helper for Remove from Knowledge (no local delete)."""
    try:
        knowledge_service.remove(knowledge_id)
        message = "Removed from Knowledge Base. Local files were not deleted."
        parameter = "success"
    except KnowledgeError as exc:
        message = str(exc)
        parameter = "error"
    return RedirectResponse(
        url=f"/?{parameter}={quote(message)}#knowledge-documents",
        status_code=303,
    )
