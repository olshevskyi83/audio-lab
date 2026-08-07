from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.lessons.assembly import format_timestamp
from app.lessons.auth import require_live_token
from app.lessons.config import settings
from app.lessons.service import LessonError, lesson_service


router = APIRouter(prefix="/api/live", tags=["live-lesson"])


class StartLessonRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    language: str = Field(default="auto", max_length=32)


def _http_error(exc: LessonError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/agent")
async def live_agent_status() -> dict[str, Any]:
    return await lesson_service.agent_status()


@router.get("/sessions")
async def list_live_sessions(
    limit: int = Query(default=10, ge=1, le=100),
) -> dict[str, Any]:
    sessions = await lesson_service.list_sessions(limit=limit)
    return {"sessions": sessions, "total": len(sessions)}


@router.get("/sessions/active")
async def get_active_live_session() -> dict[str, Any]:
    session = await lesson_service.get_active_session()
    return {"session": session}


@router.get("/sessions/{session_id}")
async def get_live_session(session_id: str) -> dict[str, Any]:
    try:
        return await lesson_service.get_session(session_id)
    except LessonError as exc:
        raise _http_error(exc) from exc


@router.delete("/sessions/{session_id}")
async def delete_live_session(session_id: str) -> dict[str, Any]:
    try:
        return await lesson_service.delete_lesson(session_id)
    except LessonError as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/start")
async def start_live_session(body: StartLessonRequest) -> dict[str, Any]:
    try:
        return await lesson_service.start_lesson(
            title=body.title,
            language=body.language,
        )
    except LessonError as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/pause")
async def pause_live_session(session_id: str) -> dict[str, Any]:
    try:
        return await lesson_service.pause_lesson(session_id)
    except LessonError as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/resume")
async def resume_live_session(session_id: str) -> dict[str, Any]:
    try:
        return await lesson_service.resume_lesson(session_id)
    except LessonError as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/stop")
async def stop_live_session(session_id: str) -> dict[str, Any]:
    try:
        return await lesson_service.stop_lesson(session_id)
    except LessonError as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/cancel")
async def cancel_live_session(session_id: str) -> dict[str, Any]:
    try:
        return await lesson_service.cancel_lesson(session_id)
    except LessonError as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/poll")
async def poll_live_session(session_id: str) -> dict[str, Any]:
    try:
        return await lesson_service.poll_session_transcriptions(session_id)
    except LessonError as exc:
        raise _http_error(exc) from exc


@router.get("/sessions/{session_id}/audio/{filename}")
async def download_live_chunk_audio(
    session_id: str,
    filename: str,
) -> FileResponse:
    try:
        path = lesson_service.store.audio_file_path(session_id, filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Audio file not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return FileResponse(
        path,
        media_type="audio/wav",
        filename=path.name,
    )


@router.get(
    "/sessions/{session_id}/lesson.txt",
    response_class=PlainTextResponse,
)
async def download_lesson_txt(
    session_id: str,
    download: bool = Query(default=False),
) -> PlainTextResponse:
    try:
        session = lesson_service.store.load(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    path = lesson_service.store.lesson_txt_path(session_id)
    if not path.is_file():
        lesson_service._rebuild_lesson_txt(session)
        path = lesson_service.store.lesson_txt_path(session_id)

    disposition = "attachment" if download else "inline"
    return PlainTextResponse(
        path.read_text(encoding="utf-8", errors="replace"),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'{disposition}; filename="lesson.txt"',
        },
    )


@router.post("/sessions/{session_id}/chunks")
async def upload_live_chunk(
    session_id: str,
    audio: UploadFile = File(...),
    chunk_index: int = Form(...),
    start_offset_seconds: float = Form(...),
    end_offset_seconds: float = Form(...),
    duration_seconds: float = Form(...),
    authorization: str | None = Header(default=None),
    x_live_token: str | None = Header(default=None, alias="X-Live-Token"),
) -> dict[str, Any]:
    require_live_token(
        expected=settings.live_upload_token,
        authorization=authorization,
        x_live_token=x_live_token,
    )

    data = await audio.read()
    try:
        return await lesson_service.accept_chunk(
            session_id=session_id,
            chunk_index=chunk_index,
            start_offset_seconds=start_offset_seconds,
            end_offset_seconds=end_offset_seconds,
            duration_seconds=duration_seconds,
            audio=data,
            filename=audio.filename,
        )
    except LessonError as exc:
        raise _http_error(exc) from exc
    finally:
        await audio.close()
