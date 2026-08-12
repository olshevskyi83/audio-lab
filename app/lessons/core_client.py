from __future__ import annotations

from typing import Any

import httpx


class CoreTaskClient:
    """Direct Homelab Core task API client for live lesson chunks."""

    def __init__(self, *, base_url: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def create_whisper_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = {
            "type": "whisper",
            "provider": "auto",
            "priority": "normal",
            "payload": payload,
            "max_attempts": 3,
        }
        return await self._request("POST", "/tasks", json=body)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        try:
            return await self._request("GET", f"/tasks/{task_id}")
        except CoreClientError as exc:
            if exc.status_code == 404:
                return None
            raise

    async def cancel_task(self, task_id: str) -> dict[str, Any] | None:
        try:
            return await self._request("POST", f"/tasks/{task_id}/cancel")
        except CoreClientError as exc:
            if exc.status_code in {404, 409}:
                return None
            raise

    async def register_knowledge_document(
        self,
        *,
        document_id: str,
        text_path: str,
        source_filename: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/knowledge/transcriptions",
            json={
                "document_id": document_id,
                "text_path": text_path,
                "source_filename": source_filename,
            },
        )

    async def index_knowledge_document(
        self,
        document_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/knowledge/documents/{document_id}/index",
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    json=json,
                )
        except httpx.HTTPError as exc:
            raise CoreClientError(
                f"Homelab Core unreachable: {exc}"
            ) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {"detail": response.text[:1000]}

        if response.status_code >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            raise CoreClientError(
                str(detail or payload),
                status_code=response.status_code,
            )

        if not isinstance(payload, dict):
            raise CoreClientError("Invalid Homelab Core response")

        return payload


class CoreClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
