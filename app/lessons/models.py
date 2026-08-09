from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


class SessionStatus(StrEnum):
    CREATED = "created"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPING = "stopping"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ChunkStatus(StrEnum):
    RECORDING = "recording"
    UPLOADED = "uploaded"
    TRANSCRIBING = "transcribing"
    TRANSCRIBED = "transcribed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_SESSION_STATUSES = {
    SessionStatus.COMPLETED,
    SessionStatus.CANCELLED,
    SessionStatus.FAILED,
}

ACTIVE_CAPTURE_STATUSES = {
    SessionStatus.RECORDING,
    SessionStatus.PAUSED,
}

CHUNK_ACCEPT_STATUSES = {
    SessionStatus.RECORDING,
    SessionStatus.PAUSED,
    SessionStatus.STOPPING,
    SessionStatus.PROCESSING,
}


class LessonChunk(BaseModel):
    chunk_index: int = Field(ge=1)
    filename: str
    start_offset_seconds: float = Field(ge=0)
    end_offset_seconds: float = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    status: ChunkStatus = ChunkStatus.UPLOADED
    audio_path: str
    whisper_task_id: str | None = None
    transcript_path: str | None = None
    error: str | None = None
    created_at: str
    completed_at: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "chunk_index": self.chunk_index,
            "filename": self.filename,
            "start_offset_seconds": self.start_offset_seconds,
            "end_offset_seconds": self.end_offset_seconds,
            "duration_seconds": self.duration_seconds,
            "status": self.status.value,
            "audio_path": self.audio_path,
            "transcript_path": self.transcript_path,
            "error": self.error,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "has_transcript": self.status == ChunkStatus.TRANSCRIBED,
        }


class LessonSession(BaseModel):
    session_id: str
    title: str
    language: str = "auto"
    status: SessionStatus = SessionStatus.CREATED
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    wall_duration_seconds: float = 0.0
    captured_duration_seconds: float = 0.0
    paused_duration_seconds: float = 0.0
    chunk_count: int = 0
    chunks: list[LessonChunk] = Field(default_factory=list)
    error: str | None = None
    pause_started_at: str | None = None
    accumulated_paused_seconds: float = 0.0
    lesson_txt_path: str | None = None

    @classmethod
    def create(
        cls,
        *,
        title: str,
        language: str = "auto",
        session_id: str | None = None,
    ) -> LessonSession:
        now = utc_now_iso()
        return cls(
            session_id=session_id or str(uuid4()),
            title=title.strip() or "Untitled recording",
            language=(language or "auto").strip() or "auto",
            status=SessionStatus.CREATED,
            created_at=now,
        )

    def chunk_by_index(self, chunk_index: int) -> LessonChunk | None:
        for chunk in self.chunks:
            if chunk.chunk_index == chunk_index:
                return chunk
        return None

    def refresh_durations(self, *, now: datetime | None = None) -> None:
        current = now or utc_now()
        if self.started_at is None:
            self.wall_duration_seconds = 0.0
            return

        started = datetime.fromisoformat(self.started_at)
        end = (
            datetime.fromisoformat(self.ended_at)
            if self.ended_at
            else current
        )
        self.wall_duration_seconds = max(
            0.0,
            (end - started).total_seconds(),
        )

        paused = self.accumulated_paused_seconds
        if self.pause_started_at and self.status == SessionStatus.PAUSED:
            pause_started = datetime.fromisoformat(self.pause_started_at)
            paused += max(0.0, (current - pause_started).total_seconds())

        self.paused_duration_seconds = paused
        self.captured_duration_seconds = sum(
            chunk.duration_seconds
            for chunk in self.chunks
            if chunk.status
            not in {ChunkStatus.CANCELLED, ChunkStatus.RECORDING}
        )
        self.chunk_count = len(self.chunks)

    def to_public_dict(self, *, include_task_ids: bool = False) -> dict[str, Any]:
        self.refresh_durations()
        chunks = []
        for chunk in sorted(self.chunks, key=lambda item: item.chunk_index):
            item = chunk.to_public_dict()
            if include_task_ids:
                item["whisper_task_id"] = chunk.whisper_task_id
            chunks.append(item)

        transcribed = sum(
            1 for chunk in self.chunks if chunk.status == ChunkStatus.TRANSCRIBED
        )
        pending = sum(
            1
            for chunk in self.chunks
            if chunk.status
            in {
                ChunkStatus.UPLOADED,
                ChunkStatus.TRANSCRIBING,
                ChunkStatus.RECORDING,
            }
        )
        failed = sum(
            1 for chunk in self.chunks if chunk.status == ChunkStatus.FAILED
        )

        current_pause_seconds = 0.0
        if self.pause_started_at and self.status == SessionStatus.PAUSED:
            pause_started = datetime.fromisoformat(self.pause_started_at)
            current_pause_seconds = max(
                0.0,
                (utc_now() - pause_started).total_seconds(),
            )

        has_transcript = bool(self.lesson_txt_path)
        return {
            "session_id": self.session_id,
            "recording_id": self.session_id,
            "resource_type": "recording",
            "title": self.title,
            "language": self.language,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "wall_duration_seconds": self.wall_duration_seconds,
            "captured_duration_seconds": self.captured_duration_seconds,
            "paused_duration_seconds": self.paused_duration_seconds,
            "pause_started_at": self.pause_started_at,
            "current_pause_seconds": current_pause_seconds,
            "chunk_count": self.chunk_count,
            "transcribed_chunk_count": transcribed,
            "pending_chunk_count": pending,
            "failed_chunk_count": failed,
            "error": self.error,
            "lesson_txt_path": self.lesson_txt_path,
            "has_lesson_txt": has_transcript,
            "has_recording_transcript": has_transcript,
            "knowledge": {
                "status": "not_indexed",
                "available": True,
                "indexed": False,
                "knowledge_id": None,
                "document_id": None,
                "message": "Не в базі знань",
                "can_add": True,
                "can_reindex": False,
                "can_remove": False,
            },
            "chunks": chunks,
        }
