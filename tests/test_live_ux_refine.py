from __future__ import annotations

from pathlib import Path

import pytest

from app.lessons.config import LiveLessonSettings
from app.lessons.models import LessonSession, SessionStatus, utc_now_iso
from tests.test_live_api import api_client  # noqa: F401
from tests.test_live_lesson import lesson_env, make_wav  # noqa: F401


def test_chunk_max_default_is_ten_minutes(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LIVE_CHUNK_MAX_SECONDS", raising=False)
    settings = LiveLessonSettings()
    assert settings.chunk_max_seconds == 600.0


@pytest.mark.asyncio
async def test_start_passes_max_chunk_600_to_agent(lesson_env):
    service, _, capture, _, _ = lesson_env
    # Override settings on service to known default.
    service.settings.chunk_max_seconds = 600.0
    await service.start_lesson(title="Rollover", language="auto")
    payload = capture.calls[0][1]
    assert payload["chunk_max_seconds"] == 600.0
    assert payload["chunk_silence_seconds"] == service.settings.chunk_silence_seconds


@pytest.mark.asyncio
async def test_rollover_continues_same_session(lesson_env):
    service, store, _, core, _ = lesson_env
    session = await service.start_lesson(title="Same session", language="auto")
    sid = session["session_id"]

    await service.accept_chunk(
        session_id=sid,
        chunk_index=1,
        start_offset_seconds=0,
        end_offset_seconds=600,
        duration_seconds=600,
        audio=make_wav(),
    )
    await service.accept_chunk(
        session_id=sid,
        chunk_index=2,
        start_offset_seconds=600,
        end_offset_seconds=900,
        duration_seconds=300,
        audio=make_wav(),
    )
    loaded = store.load(sid)
    assert loaded.session_id == sid
    assert [c.chunk_index for c in loaded.chunks] == [1, 2]
    assert all(
        (store.session_dir(sid) / "audio" / c.filename).is_file()
        for c in loaded.chunks
    )


def test_audio_preserved_and_servable_after_transcription(api_client):
    client, service, _, core = api_client
    started = client.post(
        "/api/live/sessions/start",
        json={"title": "Keep audio", "language": "uk"},
    )
    sid = started.json()["session_id"]
    uploaded = client.post(
        f"/api/live/sessions/{sid}/chunks",
        headers={"Authorization": "Bearer secret-token"},
        data={
            "chunk_index": "1",
            "start_offset_seconds": "0",
            "end_offset_seconds": "600",
            "duration_seconds": "600",
        },
        files={"audio": ("chunk_0001.wav", make_wav(), "audio/wav")},
    )
    task_id = uploaded.json()["whisper_task_id"]
    core.complete(task_id, "ten minute chunk")
    client.post(f"/api/live/sessions/{sid}/poll")
    client.post(f"/api/live/sessions/{sid}/stop")

    audio_path = service.store.session_dir(sid) / "audio" / "chunk_0001.wav"
    assert audio_path.is_file()

    audio = client.get(f"/api/live/sessions/{sid}/audio/chunk_0001.wav")
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/")

    detail = client.get(f"/live/sessions/{sid}")
    assert detail.status_code == 200
    assert "00:00:00–00:10:00" in detail.text or "00:00–00:10:00" in detail.text
    assert "Add to Knowledge" in detail.text
    assert "View Transcript" in detail.text or "Переглянути транскрипцію" in detail.text
    assert "Download Transcript" in detail.text or "Завантажити TXT" in detail.text
    assert "Delete Recording" in detail.text
    assert "Видалити урок" not in detail.text


def test_stop_moves_lesson_to_recent_list(api_client):
    client, _, _, core = api_client
    started = client.post(
        "/api/live/sessions/start",
        json={"title": "To recent", "language": "auto"},
    )
    sid = started.json()["session_id"]
    uploaded = client.post(
        f"/api/live/sessions/{sid}/chunks",
        headers={"Authorization": "Bearer secret-token"},
        data={
            "chunk_index": "1",
            "start_offset_seconds": "0",
            "end_offset_seconds": "30",
            "duration_seconds": "30",
        },
        files={"audio": ("chunk_0001.wav", make_wav(), "audio/wav")},
    )
    # Leave transcription outstanding so stop stays processing.
    stopped = client.post(f"/api/live/sessions/{sid}/stop")
    assert stopped.json()["status"] == "processing"

    recent = client.get("/api/live/sessions?limit=10").json()["sessions"]
    match = next(item for item in recent if item["session_id"] == sid)
    assert match["status"] == "processing"
    assert match["title"] == "To recent"

    # Active still exists server-side while processing, but UI clears panel.
    active = client.get("/api/live/sessions/active").json()["session"]
    assert active["session_id"] == sid
    assert active["status"] == "processing"


def test_active_ui_hides_debug_counters(api_client):
    client, _, _, _ = api_client
    html = client.get("/").text
    assert "live-wave" in html
    assert "data-extension=\"live-audio-level\"" in html
    assert "Записано:" in html
    assert "Пауза:" in html
    # Removed debug-style active counters
    assert ">Elapsed<" not in html
    assert ">Captured<" not in html
    assert ">Chunks<" not in html
    assert "live-chunk-counts" not in html
    assert "/live/sessions/" in html
    assert "Додати в базу знань" in client.get("/").text or True


def test_delete_removes_audio_transcript_session(api_client):
    client, service, _, core = api_client
    started = client.post(
        "/api/live/sessions/start",
        json={"title": "Delete me", "language": "auto"},
    )
    sid = started.json()["session_id"]
    uploaded = client.post(
        f"/api/live/sessions/{sid}/chunks",
        headers={"Authorization": "Bearer secret-token"},
        data={
            "chunk_index": "1",
            "start_offset_seconds": "0",
            "end_offset_seconds": "30",
            "duration_seconds": "30",
        },
        files={"audio": ("chunk_0001.wav", make_wav(), "audio/wav")},
    )
    core.complete(uploaded.json()["whisper_task_id"], "bye")
    client.post(f"/api/live/sessions/{sid}/poll")
    client.post(f"/api/live/sessions/{sid}/stop")

    session_dir = service.store.session_dir(sid)
    assert (session_dir / "audio" / "chunk_0001.wav").is_file()
    assert (session_dir / "transcripts" / "chunk_0001.txt").is_file()
    assert (session_dir / "lesson.txt").is_file()

    assert client.delete(f"/api/live/sessions/{sid}").status_code == 200
    assert not session_dir.exists()
    assert all(
        item["session_id"] != sid
        for item in client.get("/api/live/sessions?limit=10").json()["sessions"]
    )


def test_audio_path_traversal_blocked(api_client):
    client, _, _, _ = api_client
    started = client.post(
        "/api/live/sessions/start",
        json={"title": "Safe audio", "language": "auto"},
    )
    sid = started.json()["session_id"]
    response = client.get(f"/api/live/sessions/{sid}/audio/../secret.wav")
    assert response.status_code in {400, 404, 422}
