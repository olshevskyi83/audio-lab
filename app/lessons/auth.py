from __future__ import annotations

from fastapi import Header, HTTPException


def extract_bearer_token(
    authorization: str | None,
    x_live_token: str | None = None,
) -> str | None:
    if x_live_token and x_live_token.strip():
        return x_live_token.strip()
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def require_live_token(
    *,
    expected: str,
    authorization: str | None = None,
    x_live_token: str | None = None,
) -> None:
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Live upload token is not configured",
        )
    provided = extract_bearer_token(authorization, x_live_token)
    if not provided or provided != expected:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
        )


async def live_auth_dependency(
    authorization: str | None = Header(default=None),
    x_live_token: str | None = Header(default=None, alias="X-Live-Token"),
) -> None:
    # Imported lazily to avoid circular imports during settings reload in tests.
    from app.lessons.config import settings

    require_live_token(
        expected=settings.live_upload_token,
        authorization=authorization,
        x_live_token=x_live_token,
    )
