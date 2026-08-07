from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from app.knowledge.models import KnowledgeSourceType
from app.knowledge.service import KnowledgeError, KnowledgeService, knowledge_service as default_knowledge
from app.lessons.assembly import assemble_from_texts
from app.lessons.capture_client import CaptureAgentClient, CaptureAgentError
from app.lessons.config import LiveLessonSettings, settings as default_settings
from app.lessons.core_client import CoreClientError, CoreTaskClient
from app.lessons.models import (
    CHUNK_ACCEPT_STATUSES,
    TERMINAL_SESSION_STATUSES,
    ChunkStatus,
    LessonChunk,
    LessonSession,
    SessionStatus,
    utc_now,
    utc_now_iso,
)
from app.lessons.storage import SessionStore

logger = logging.getLogger("audio_lab.lessons")


class LessonError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class LessonService:
    def __init__(
        self,
        *,
        settings: LiveLessonSettings | None = None,
        store: SessionStore | None = None,
        capture_client: CaptureAgentClient | None = None,
        core_client: CoreTaskClient | None = None,
        knowledge: KnowledgeService | None = None,
    ) -> None:
        self.settings = settings or default_settings
        self.store = store or SessionStore(self.settings.sessions_root)
        self.knowledge = knowledge or default_knowledge
        self.capture = capture_client or CaptureAgentClient(
            base_url=self.settings.mac_capture_agent_url,
            token=self.settings.mac_capture_agent_token,
        )
        self.core = core_client or CoreTaskClient(
            base_url=self.settings.core_url,
        )
        self._lock = asyncio.Lock()
        self._poll_task: asyncio.Task[None] | None = None

    def ensure_ready(self) -> None:
        self.store.ensure_root()
        self.knowledge.ensure_ready()
        processing = self.settings.audio_root / "processing"
        processing.mkdir(parents=True, exist_ok=True)

    async def start_background_poller(self) -> None:
        if self._poll_task and not self._poll_task.done():
            return
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop_background_poller(self) -> None:
        if self._poll_task is None:
            return
        self._poll_task.cancel()
        try:
            await self._poll_task
        except asyncio.CancelledError:
            pass
        self._poll_task = None

    async def recover_sessions(self) -> None:
        self.ensure_ready()
        for session in self.store.list_sessions():
            if session.status in TERMINAL_SESSION_STATUSES:
                continue
            try:
                await self.poll_session_transcriptions(session.session_id)
                session = self.store.load(session.session_id)
                if session.status == SessionStatus.RECORDING:
                    # After restart we cannot assume capture still runs.
                    session.status = SessionStatus.PAUSED
                    session.pause_started_at = session.pause_started_at or utc_now_iso()
                    session.error = (
                        "Audio Lab restarted during recording; "
                        "session paused. Resume if capture agent is ready."
                    )
                    self.store.save(session)
                elif session.status in {
                    SessionStatus.STOPPING,
                    SessionStatus.PROCESSING,
                }:
                    await self._finalize_if_ready(session.session_id)
            except Exception:
                logger.exception(
                    "Failed recovering session %s",
                    session.session_id,
                )

    def _public(self, session) -> dict[str, Any]:
        session.refresh_durations()
        payload = session.to_public_dict()
        has_transcript = self.store.has_lesson_txt(session.session_id)
        payload["has_lesson_txt"] = has_transcript
        payload["has_recording_transcript"] = has_transcript
        allow_add = session.status == SessionStatus.COMPLETED and has_transcript
        knowledge = self.knowledge.state_for_source(
            KnowledgeSourceType.LIVE_RECORDING,
            session.session_id,
            allow_add=allow_add,
        )
        if session.status != SessionStatus.COMPLETED:
            knowledge["can_add"] = False
            knowledge["available"] = knowledge["indexed"]
        payload["knowledge"] = knowledge
        return payload

    async def list_sessions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return [
            self._public(session)
            for session in self.store.list_sessions()[:limit]
        ]

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return self._public(self._load(session_id))

    async def delete_lesson(self, session_id: str) -> dict[str, Any]:
        """Delete local recording assets only. Never removes Knowledge data."""
        async with self._lock:
            session = self._load(session_id)
            if session.status not in {
                SessionStatus.COMPLETED,
                SessionStatus.CANCELLED,
                SessionStatus.FAILED,
            }:
                raise LessonError(
                    "Cannot delete an in-progress live recording; cancel first",
                    status_code=409,
                )
            knowledge_before = self.knowledge.state_for_source(
                KnowledgeSourceType.LIVE_RECORDING,
                session_id,
                allow_add=False,
            )
            try:
                self.store.delete_session(session_id)
            except ValueError as exc:
                raise LessonError(str(exc), status_code=400) from exc
            except FileNotFoundError as exc:
                raise LessonError("Recording not found", status_code=404) from exc

            preserved = self.knowledge.mark_local_deleted(
                KnowledgeSourceType.LIVE_RECORDING,
                session_id,
            )
            return {
                "session_id": session_id,
                "recording_id": session_id,
                "deleted": True,
                "local_assets_deleted": True,
                "knowledge_removed": False,
                "knowledge_preserved": bool(preserved),
                "knowledge_id": (
                    knowledge_before.get("knowledge_id")
                    if knowledge_before.get("indexed")
                    else None
                ),
            }

    async def add_to_knowledge(self, session_id: str) -> dict[str, Any]:
        session = self._load(session_id)
        if session.status != SessionStatus.COMPLETED:
            raise LessonError(
                "Only completed recordings can be added to Knowledge",
                status_code=409,
            )
        if not self.store.has_lesson_txt(session_id):
            raise LessonError(
                "Recording transcript is not ready yet",
                status_code=409,
            )
        doc = self.knowledge.add_for_live_recording(
            session_id=session_id,
            title=session.title,
        )
        payload = self._public(session)
        payload["knowledge_document"] = doc
        return payload

    async def reindex_knowledge(self, session_id: str) -> dict[str, Any]:
        session = self._load(session_id)
        if session.status != SessionStatus.COMPLETED:
            raise LessonError(
                "Only completed recordings can be reindexed",
                status_code=409,
            )
        try:
            doc = self.knowledge.reindex_for_live_recording(
                session_id=session_id,
                title=session.title,
            )
        except KnowledgeError as exc:
            raise LessonError(str(exc), status_code=exc.status_code) from exc
        payload = self._public(session)
        payload["knowledge_document"] = doc
        return payload

    async def remove_from_knowledge(self, session_id: str) -> dict[str, Any]:
        """Remove Knowledge registration only. Local recording remains."""
        session = self._load(session_id)
        removed = self.knowledge.remove_by_source(
            KnowledgeSourceType.LIVE_RECORDING,
            session_id,
        )
        if removed is None:
            raise LessonError(
                "Recording is not in the Knowledge Base",
                status_code=404,
            )
        payload = self._public(session)
        payload["knowledge_removed"] = removed
        return payload

    async def get_active_session(self) -> dict[str, Any] | None:
        for session in self.store.list_sessions():
            if session.status in {
                SessionStatus.CREATED,
                SessionStatus.RECORDING,
                SessionStatus.PAUSED,
                SessionStatus.STOPPING,
                SessionStatus.PROCESSING,
                SessionStatus.CANCELLING,
            }:
                return self._public(session)
        return None

    async def start_lesson(
        self,
        *,
        title: str,
        language: str = "auto",
    ) -> dict[str, Any]:
        async with self._lock:
            active = await self.get_active_session()
            if active and active["status"] not in {
                SessionStatus.COMPLETED.value,
                SessionStatus.CANCELLED.value,
                SessionStatus.FAILED.value,
            }:
                raise LessonError(
                    "Another live lesson is already active",
                    status_code=409,
                )

            session = LessonSession.create(title=title, language=language)
            self.store.save(session)

            try:
                await self.capture.start_session(
                    {
                        "session_id": session.session_id,
                        "title": session.title,
                        "language": session.language,
                        "started_at": utc_now_iso(),
                        "upload_url": (
                            f"{self.settings.audio_lab_public_url}"
                            f"/api/live/sessions/{session.session_id}/chunks"
                        ),
                        "chunk_silence_seconds": self.settings.chunk_silence_seconds,
                        "chunk_min_seconds": self.settings.chunk_min_seconds,
                        "chunk_max_seconds": self.settings.chunk_max_seconds,
                    }
                )
            except CaptureAgentError as exc:
                session.status = SessionStatus.FAILED
                session.error = str(exc)
                session.ended_at = utc_now_iso()
                self.store.save(session)
                raise LessonError(str(exc), status_code=exc.status_code or 502) from exc

            now = utc_now_iso()
            session.status = SessionStatus.RECORDING
            session.started_at = now
            session.error = None
            self.store.save(session)
            return self._public(session)

    async def pause_lesson(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            session = self._load(session_id)
            if session.status != SessionStatus.RECORDING:
                raise LessonError(
                    f"Cannot pause session in status {session.status.value}",
                    status_code=409,
                )
            try:
                await self.capture.pause(session_id)
            except CaptureAgentError as exc:
                raise LessonError(str(exc), status_code=exc.status_code or 502) from exc

            session.status = SessionStatus.PAUSED
            session.pause_started_at = utc_now_iso()
            self.store.save(session)
            return self._public(session)

    async def resume_lesson(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            session = self._load(session_id)
            if session.status != SessionStatus.PAUSED:
                raise LessonError(
                    f"Cannot resume session in status {session.status.value}",
                    status_code=409,
                )
            try:
                await self.capture.resume(session_id)
            except CaptureAgentError as exc:
                raise LessonError(str(exc), status_code=exc.status_code or 502) from exc

            if session.pause_started_at:
                paused_for = (
                    utc_now()
                    - datetime.fromisoformat(session.pause_started_at)
                ).total_seconds()
                session.accumulated_paused_seconds += max(0.0, paused_for)

            session.pause_started_at = None
            session.status = SessionStatus.RECORDING
            session.error = None
            self.store.save(session)
            return self._public(session)

    async def stop_lesson(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            session = self._load(session_id)
            if session.status not in {
                SessionStatus.RECORDING,
                SessionStatus.PAUSED,
            }:
                raise LessonError(
                    f"Cannot stop session in status {session.status.value}",
                    status_code=409,
                )

            session.status = SessionStatus.STOPPING
            self.store.save(session)

            try:
                await self.capture.stop(session_id)
            except CaptureAgentError as exc:
                # Capture may already be stopped; continue finalization.
                session.error = f"Capture stop warning: {exc}"

            if session.pause_started_at:
                paused_for = (
                    utc_now()
                    - datetime.fromisoformat(session.pause_started_at)
                ).total_seconds()
                session.accumulated_paused_seconds += max(0.0, paused_for)
                session.pause_started_at = None

            session.status = SessionStatus.PROCESSING
            self.store.save(session)

        await self.poll_session_transcriptions(session_id)
        return await self._finalize_if_ready(session_id)

    async def cancel_lesson(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            session = self._load(session_id)
            if session.status in TERMINAL_SESSION_STATUSES:
                raise LessonError(
                    f"Session already {session.status.value}",
                    status_code=409,
                )

            session.status = SessionStatus.CANCELLING
            self.store.save(session)

            try:
                await self.capture.cancel(session_id)
            except CaptureAgentError:
                pass

            for chunk in session.chunks:
                if chunk.whisper_task_id and chunk.status in {
                    ChunkStatus.UPLOADED,
                    ChunkStatus.TRANSCRIBING,
                }:
                    try:
                        await self.core.cancel_task(chunk.whisper_task_id)
                    except CoreClientError:
                        pass
                    chunk.status = ChunkStatus.CANCELLED
                elif chunk.status in {
                    ChunkStatus.UPLOADED,
                    ChunkStatus.TRANSCRIBING,
                    ChunkStatus.RECORDING,
                    ChunkStatus.FAILED,
                }:
                    chunk.status = ChunkStatus.CANCELLED
                    if chunk.filename:
                        try:
                            self.store.delete_audio_file(
                                session.session_id,
                                chunk.filename,
                            )
                        except OSError:
                            pass

            session.chunks = [
                chunk
                for chunk in session.chunks
                if chunk.status == ChunkStatus.TRANSCRIBED
            ]
            session.chunk_count = len(session.chunks)
            session.status = SessionStatus.CANCELLED
            session.ended_at = utc_now_iso()
            session.pause_started_at = None
            self.store.save(session)
            self._rebuild_lesson_txt(session)
            return self._public(session)

    async def accept_chunk(
        self,
        *,
        session_id: str,
        chunk_index: int,
        start_offset_seconds: float,
        end_offset_seconds: float,
        duration_seconds: float,
        audio: bytes,
        filename: str | None = None,
    ) -> dict[str, Any]:
        if chunk_index < 1:
            raise LessonError("chunk_index must be >= 1")
        if duration_seconds <= 0 or end_offset_seconds < start_offset_seconds:
            raise LessonError("Invalid chunk timeline")
        if abs(duration_seconds - (end_offset_seconds - start_offset_seconds)) > 1.0:
            # Allow small clock skew; reject large inconsistencies.
            if end_offset_seconds - start_offset_seconds <= 0:
                raise LessonError("Invalid chunk offsets")
        if len(audio) == 0:
            raise LessonError("Empty audio chunk rejected")
        if len(audio) > self.settings.max_chunk_bytes:
            raise LessonError("Chunk exceeds size limit", status_code=413)
        if not self._looks_like_wav(audio):
            raise LessonError("Audio must be WAV PCM")

        async with self._lock:
            session = self._load(session_id)
            if session.status not in CHUNK_ACCEPT_STATUSES:
                raise LessonError(
                    f"Session status {session.status.value} does not accept chunks",
                    status_code=409,
                )

            existing = session.chunk_by_index(chunk_index)
            if existing is not None:
                raise LessonError(
                    f"Duplicate chunk_index {chunk_index}",
                    status_code=409,
                )

            wav_name = filename or f"chunk_{chunk_index:04d}.wav"
            if "/" in wav_name or "\\" in wav_name or wav_name != Path(wav_name).name:
                raise LessonError("Invalid audio filename")
            wav_name = Path(wav_name).name
            if not wav_name.endswith(".wav"):
                raise LessonError("Audio filename must end with .wav")

            audio_path = self.store.write_audio_bytes(
                session_id,
                wav_name,
                audio,
            )

            chunk = LessonChunk(
                chunk_index=chunk_index,
                filename=wav_name,
                start_offset_seconds=float(start_offset_seconds),
                end_offset_seconds=float(end_offset_seconds),
                duration_seconds=float(duration_seconds),
                status=ChunkStatus.UPLOADED,
                audio_path=f"audio/{wav_name}",
                created_at=utc_now_iso(),
            )
            session.chunks.append(chunk)
            session.chunks.sort(key=lambda item: item.chunk_index)
            session.chunk_count = len(session.chunks)
            self.store.save(session)

            try:
                task_id = await self._enqueue_whisper(session, chunk, audio_path)
            except Exception as exc:
                chunk.status = ChunkStatus.FAILED
                chunk.error = str(exc)
                self.store.save(session)
                raise LessonError(
                    f"Failed to enqueue Whisper task: {exc}",
                    status_code=502,
                ) from exc

            chunk.whisper_task_id = task_id
            chunk.status = ChunkStatus.TRANSCRIBING
            self.store.save(session)
            return {
                "session_id": session_id,
                "chunk_index": chunk_index,
                "status": chunk.status.value,
                "whisper_task_id": task_id,
            }

    async def poll_session_transcriptions(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            session = self._load(session_id)
            changed = False

            for chunk in session.chunks:
                if chunk.status != ChunkStatus.TRANSCRIBING or not chunk.whisper_task_id:
                    continue
                try:
                    task = await self.core.get_task(chunk.whisper_task_id)
                except CoreClientError as exc:
                    chunk.error = str(exc)
                    changed = True
                    continue

                if task is None:
                    chunk.status = ChunkStatus.FAILED
                    chunk.error = "Whisper task missing"
                    changed = True
                    continue

                status = task.get("status")
                if status == "completed":
                    result = task.get("result") or {}
                    text = str(result.get("text") or "").strip()
                    transcript_name = f"chunk_{chunk.chunk_index:04d}.txt"
                    self.store.write_transcript_text(
                        session_id,
                        transcript_name,
                        text,
                    )
                    chunk.transcript_path = f"transcripts/{transcript_name}"
                    chunk.status = ChunkStatus.TRANSCRIBED
                    chunk.completed_at = utc_now_iso()
                    chunk.error = None
                    changed = True
                elif status == "failed":
                    chunk.status = ChunkStatus.FAILED
                    chunk.error = str(task.get("error") or "Whisper failed")
                    chunk.completed_at = utc_now_iso()
                    changed = True
                elif status == "cancelled":
                    chunk.status = ChunkStatus.CANCELLED
                    chunk.completed_at = utc_now_iso()
                    changed = True

            if changed:
                self._rebuild_lesson_txt(session)
                self.store.save(session)

        if self._load(session_id).status == SessionStatus.PROCESSING:
            return await self._finalize_if_ready(session_id)
        return self._public(self._load(session_id))

    async def poll_all_active(self) -> None:
        for session in self.store.list_sessions():
            if session.status in TERMINAL_SESSION_STATUSES:
                continue
            try:
                await self.poll_session_transcriptions(session.session_id)
            except Exception:
                logger.exception(
                    "Polling failed for session %s",
                    session.session_id,
                )

    async def agent_status(self) -> dict[str, Any]:
        try:
            payload = await self.capture.status()
            return {"available": True, "status": payload}
        except CaptureAgentError as exc:
            return {"available": False, "error": str(exc)}

    async def _enqueue_whisper(
        self,
        session: LessonSession,
        chunk: LessonChunk,
        audio_path: Path,
    ) -> str:
        # Core archives the file_path after transcription, so enqueue a copy.
        processing_dir = self.settings.audio_root / "processing"
        processing_dir.mkdir(parents=True, exist_ok=True)
        staging_name = (
            f"live_{session.session_id}_chunk_{chunk.chunk_index:04d}.wav"
        )
        staging_path = processing_dir / staging_name
        if staging_path.exists():
            staging_path = processing_dir / (
                f"live_{session.session_id}_chunk_{chunk.chunk_index:04d}"
                f"_{utc_now().strftime('%H%M%S')}.wav"
            )
        shutil.copy2(audio_path, staging_path)

        payload = {
            "file_path": str(staging_path),
            "language": session.language,
            "source": "live_lesson",
            "original_filename": chunk.filename,
            "session_id": session.session_id,
            "recording_title": session.title,
            "lesson_title": session.title,
            "chunk_index": chunk.chunk_index,
            "start_offset_seconds": chunk.start_offset_seconds,
            "end_offset_seconds": chunk.end_offset_seconds,
            "audio_path": str(audio_path),
        }
        task = await self.core.create_whisper_task(payload)
        task_id = task.get("id")
        if not task_id:
            raise CoreClientError("Core did not return task id")
        return str(task_id)

    async def _finalize_if_ready(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            session = self._load(session_id)
            if session.status not in {
                SessionStatus.PROCESSING,
                SessionStatus.STOPPING,
            }:
                return self._public(session)

            pending = [
                chunk
                for chunk in session.chunks
                if chunk.status
                in {
                    ChunkStatus.UPLOADED,
                    ChunkStatus.TRANSCRIBING,
                    ChunkStatus.RECORDING,
                }
            ]
            if pending:
                session.status = SessionStatus.PROCESSING
                self.store.save(session)
                return self._public(session)

            self._rebuild_lesson_txt(session)
            session.status = SessionStatus.COMPLETED
            session.ended_at = session.ended_at or utc_now_iso()
            session.error = None
            self.store.save(session)
            return self._public(session)

    def _rebuild_lesson_txt(self, session: LessonSession) -> None:
        transcripts: dict[int, str] = {}
        for chunk in session.chunks:
            if not chunk.transcript_path:
                continue
            path = self.store.session_dir(session.session_id) / chunk.transcript_path
            if path.is_file():
                transcripts[chunk.chunk_index] = path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
        text = assemble_from_texts(session, transcripts)
        self.store.write_lesson_text(session.session_id, text)
        session.lesson_txt_path = "lesson.txt"

    def _load(self, session_id: str) -> LessonSession:
        try:
            return self.store.load(session_id)
        except FileNotFoundError as exc:
            raise LessonError("Session not found", status_code=404) from exc
        except ValueError as exc:
            raise LessonError(str(exc), status_code=400) from exc

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.poll_all_active()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Live lesson poller error")
            await asyncio.sleep(self.settings.poll_interval_seconds)

    @staticmethod
    def _looks_like_wav(data: bytes) -> bool:
        return len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WAVE"


# Process-wide service instance used by FastAPI routes.
lesson_service = LessonService()
