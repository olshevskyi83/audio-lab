from __future__ import annotations

import struct
from pathlib import Path

import pytest

from app.lessons.assembly import assemble_from_texts, format_timestamp
from app.lessons.auth import require_live_token
from app.lessons.config import LiveLessonSettings
from app.lessons.models import ChunkStatus, LessonChunk, LessonSession, SessionStatus, utc_now_iso
from app.lessons.service import LessonError, LessonService
from app.lessons.storage import SessionStore


def make_wav(samples: int = 1600, sample_rate: int = 16000) -> bytes:
    data_size = samples * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        data_size,
    )
    pcm = b"\x00\x10" * samples
    return header + pcm


class FakeCapture:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def health(self):
        return {"status": "ok"}

    async def status(self):
        return {"state": "idle"}

    async def start_session(self, payload):
        self.calls.append(("start", payload))
        return {"state": "recording", "session_id": payload["session_id"]}

    async def pause(self, session_id):
        self.calls.append(("pause", session_id))
        return {"state": "paused"}

    async def resume(self, session_id):
        self.calls.append(("resume", session_id))
        return {"state": "recording"}

    async def stop(self, session_id):
        self.calls.append(("stop", session_id))
        return {"state": "idle"}

    async def cancel(self, session_id):
        self.calls.append(("cancel", session_id))
        return {"state": "cancelled"}


class FakeCore:
    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self.created: list[dict] = []
        self._n = 0

    async def create_whisper_task(self, payload):
        self._n += 1
        task_id = f"task-{self._n}"
        task = {
            "id": task_id,
            "status": "waiting",
            "payload": payload,
            "result": None,
            "error": None,
        }
        self.tasks[task_id] = task
        self.created.append(payload)
        return task

    async def get_task(self, task_id):
        return self.tasks.get(task_id)

    async def cancel_task(self, task_id):
        task = self.tasks.get(task_id)
        if not task:
            return None
        task["status"] = "cancelled"
        return task

    def complete(self, task_id: str, text: str) -> None:
        self.tasks[task_id]["status"] = "completed"
        self.tasks[task_id]["result"] = {"text": text}


@pytest.fixture
def lesson_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    audio_root = tmp_path / "audio"
    sessions = audio_root / "sessions"
    (audio_root / "processing").mkdir(parents=True)
    sessions.mkdir(parents=True)

    monkeypatch.setenv("AUDIO_ROOT", str(audio_root))
    monkeypatch.setenv("LIVE_SESSIONS_ROOT", str(sessions))
    monkeypatch.setenv("CORE_URL", "http://core.test")
    monkeypatch.setenv("MAC_CAPTURE_AGENT_URL", "http://mac.test:8011")
    monkeypatch.setenv("MAC_CAPTURE_AGENT_TOKEN", "secret-token")
    monkeypatch.setenv("LIVE_UPLOAD_TOKEN", "secret-token")
    monkeypatch.setenv("AUDIO_LAB_PUBLIC_URL", "http://lab.test")

    settings = LiveLessonSettings()
    store = SessionStore(settings.sessions_root)
    capture = FakeCapture()
    core = FakeCore()
    service = LessonService(
        settings=settings,
        store=store,
        capture_client=capture,  # type: ignore[arg-type]
        core_client=core,  # type: ignore[arg-type]
    )
    service.ensure_ready()
    return service, store, capture, core, settings


@pytest.mark.asyncio
async def test_session_creation_and_persistence(lesson_env):
    service, store, capture, core, _ = lesson_env
    session = await service.start_lesson(title="Zoom Python", language="uk")
    assert session["status"] == "recording"
    assert session["title"] == "Zoom Python"
    loaded = store.load(session["session_id"])
    assert loaded.status == SessionStatus.RECORDING
    assert capture.calls[0][0] == "start"


@pytest.mark.asyncio
async def test_pause_resume_lifecycle(lesson_env):
    service, store, capture, _, _ = lesson_env
    session = await service.start_lesson(title="Lesson", language="auto")
    sid = session["session_id"]
    paused = await service.pause_lesson(sid)
    assert paused["status"] == "paused"
    resumed = await service.resume_lesson(sid)
    assert resumed["status"] == "recording"
    assert [c[0] for c in capture.calls] == ["start", "pause", "resume"]


@pytest.mark.asyncio
async def test_stop_lifecycle_waits_for_transcription(lesson_env):
    service, store, _, core, _ = lesson_env
    session = await service.start_lesson(title="Lesson", language="auto")
    sid = session["session_id"]

    await service.accept_chunk(
        session_id=sid,
        chunk_index=1,
        start_offset_seconds=0,
        end_offset_seconds=30,
        duration_seconds=30,
        audio=make_wav(),
    )
    task_id = store.load(sid).chunks[0].whisper_task_id
    assert task_id

    stopped = await service.stop_lesson(sid)
    assert stopped["status"] == "processing"

    core.complete(task_id, "hello lesson")
    final = await service.poll_session_transcriptions(sid)
    assert final["status"] == "completed"
    lesson_path = store.lesson_txt_path(sid)
    assert lesson_path.is_file()
    text = lesson_path.read_text(encoding="utf-8")
    assert "hello lesson" in text
    assert "[00:00:00 – 00:00:30]" in text


@pytest.mark.asyncio
async def test_cancel_lifecycle(lesson_env):
    service, store, capture, core, _ = lesson_env
    session = await service.start_lesson(title="Lesson", language="auto")
    sid = session["session_id"]
    await service.accept_chunk(
        session_id=sid,
        chunk_index=1,
        start_offset_seconds=0,
        end_offset_seconds=25,
        duration_seconds=25,
        audio=make_wav(),
    )
    cancelled = await service.cancel_lesson(sid)
    assert cancelled["status"] == "cancelled"
    assert capture.calls[-1][0] == "cancel"
    # unfinished chunk removed
    assert store.load(sid).chunks == []


@pytest.mark.asyncio
async def test_chunk_ordering_and_duplicate_rejection(lesson_env):
    service, store, _, core, _ = lesson_env
    session = await service.start_lesson(title="Lesson", language="auto")
    sid = session["session_id"]

    await service.accept_chunk(
        session_id=sid,
        chunk_index=2,
        start_offset_seconds=900,
        end_offset_seconds=960,
        duration_seconds=60,
        audio=make_wav(),
    )
    await service.accept_chunk(
        session_id=sid,
        chunk_index=1,
        start_offset_seconds=0,
        end_offset_seconds=420,
        duration_seconds=420,
        audio=make_wav(),
    )
    chunks = store.load(sid).chunks
    assert [c.chunk_index for c in chunks] == [1, 2]

    with pytest.raises(LessonError, match="Duplicate"):
        await service.accept_chunk(
            session_id=sid,
            chunk_index=1,
            start_offset_seconds=0,
            end_offset_seconds=10,
            duration_seconds=10,
            audio=make_wav(),
        )


@pytest.mark.asyncio
async def test_chunk_validation_and_path_traversal(lesson_env):
    service, _, _, _, _ = lesson_env
    session = await service.start_lesson(title="Lesson", language="auto")
    sid = session["session_id"]

    with pytest.raises(LessonError, match="WAV"):
        await service.accept_chunk(
            session_id=sid,
            chunk_index=1,
            start_offset_seconds=0,
            end_offset_seconds=20,
            duration_seconds=20,
            audio=b"not-a-wav",
        )

    with pytest.raises(LessonError, match="Empty"):
        await service.accept_chunk(
            session_id=sid,
            chunk_index=1,
            start_offset_seconds=0,
            end_offset_seconds=20,
            duration_seconds=20,
            audio=b"",
        )

    with pytest.raises(LessonError, match="filename"):
        await service.accept_chunk(
            session_id=sid,
            chunk_index=1,
            start_offset_seconds=0,
            end_offset_seconds=20,
            duration_seconds=20,
            audio=make_wav(),
            filename="../evil.wav",
        )


def test_timeline_pause_gaps_in_assembly():
    session = LessonSession.create(title="Gap lesson", language="uk")
    session.chunks = [
        LessonChunk(
            chunk_index=1,
            filename="chunk_0001.wav",
            start_offset_seconds=0,
            end_offset_seconds=420,
            duration_seconds=420,
            status=ChunkStatus.TRANSCRIBED,
            audio_path="audio/chunk_0001.wav",
            transcript_path="transcripts/chunk_0001.txt",
            created_at=utc_now_iso(),
            completed_at=utc_now_iso(),
        ),
        LessonChunk(
            chunk_index=2,
            filename="chunk_0002.wav",
            start_offset_seconds=900,
            end_offset_seconds=1200,
            duration_seconds=300,
            status=ChunkStatus.TRANSCRIBED,
            audio_path="audio/chunk_0002.wav",
            transcript_path="transcripts/chunk_0002.txt",
            created_at=utc_now_iso(),
            completed_at=utc_now_iso(),
        ),
    ]
    text = assemble_from_texts(
        session,
        {
            1: "first part",
            2: "after pause",
        },
    )
    assert "[PAUSE / no captured audio]" in text
    assert "[00:00:00 – 00:07:00]" in text
    assert "[00:15:00 – 00:20:00]" in text
    assert text.index("first part") < text.index("after pause")
    assert format_timestamp(420) == "00:07:00"


@pytest.mark.asyncio
async def test_core_enqueue_metadata(lesson_env):
    service, _, _, core, _ = lesson_env
    session = await service.start_lesson(title="Meta Lesson", language="de")
    sid = session["session_id"]
    await service.accept_chunk(
        session_id=sid,
        chunk_index=1,
        start_offset_seconds=10,
        end_offset_seconds=40,
        duration_seconds=30,
        audio=make_wav(),
    )
    payload = core.created[0]
    assert payload["source"] == "live_lesson"
    assert payload["session_id"] == sid
    assert payload["lesson_title"] == "Meta Lesson"
    assert payload["chunk_index"] == 1
    assert payload["language"] == "de"
    assert Path(payload["file_path"]).name.startswith("live_")


@pytest.mark.asyncio
async def test_restart_recovery_pauses_recording(lesson_env):
    service, store, _, _, _ = lesson_env
    session = await service.start_lesson(title="Recovery", language="auto")
    sid = session["session_id"]
    await service.recover_sessions()
    loaded = store.load(sid)
    assert loaded.status == SessionStatus.PAUSED


def test_authentication_failure():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        require_live_token(expected="secret", authorization="Bearer wrong")
    assert exc.value.status_code == 401


def test_session_path_traversal_rejected(tmp_path: Path):
    store = SessionStore(tmp_path)
    with pytest.raises(ValueError):
        store.session_dir("../escape")
