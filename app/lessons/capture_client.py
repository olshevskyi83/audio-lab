from __future__ import annotations

from typing import Any

import httpx


class CaptureAgentError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class CaptureAgentClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/health")

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", "/status")

    async def start_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/sessions/start", json=payload)

    async def pause(self, session_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/sessions/{session_id}/pause")

    async def resume(self, session_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/sessions/{session_id}/resume")

    async def stop(self, session_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/sessions/{session_id}/stop")

    async def cancel(self, session_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/sessions/{session_id}/cancel")

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
                    headers=self._headers(),
                    json=json,
                )
        except httpx.HTTPError as exc:
            raise CaptureAgentError(
                f"Mac Capture Agent unreachable: {exc}"
            ) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {"detail": response.text[:1000]}

        if response.status_code >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            if isinstance(detail, dict):
                message = detail.get("message") or str(detail)
            else:
                message = str(detail or payload)
            raise CaptureAgentError(message, status_code=response.status_code)

        if not isinstance(payload, dict):
            raise CaptureAgentError("Invalid Capture Agent response")

        return payload
