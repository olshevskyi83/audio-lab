from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_live_api import api_client  # noqa: F401
from tests.test_live_lesson import lesson_env, make_wav  # noqa: F401


def _start_with_chunk(client, core, *, complete: bool = False):
    started = client.post(
        "/api/live/sessions/start",
        json={"title": "UX Lesson", "language": "uk"},
    )
    assert started.status_code == 200
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
    assert uploaded.status_code == 200
    task_id = uploaded.json()["whisper_task_id"]
    if complete:
        core.complete(task_id, "hello from lesson")
        polled = client.post(f"/api/live/sessions/{sid}/poll")
        assert polled.status_code == 200
    return sid, task_id


def test_completed_session_remains_in_list_and_active_clears(api_client):
    client, _, _, core = api_client
    sid, _ = _start_with_chunk(client, core, complete=True)

    stopped = client.post(f"/api/live/sessions/{sid}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "completed"
    assert stopped.json()["has_lesson_txt"] is True

    active = client.get("/api/live/sessions/active")
    assert active.status_code == 200
    assert active.json()["session"] is None

    listed = client.get("/api/live/sessions?limit=10")
    assert listed.status_code == 200
    sessions = listed.json()["sessions"]
    assert any(item["session_id"] == sid for item in sessions)
    match = next(item for item in sessions if item["session_id"] == sid)
    assert match["status"] == "completed"
    assert match["has_lesson_txt"] is True
    assert match["title"] == "UX Lesson"


def test_transcript_download_available_after_stop(api_client):
    client, _, _, core = api_client
    sid, _ = _start_with_chunk(client, core, complete=True)
    client.post(f"/api/live/sessions/{sid}/stop")

    view = client.get(f"/api/live/sessions/{sid}/lesson.txt")
    assert view.status_code == 200
    assert "hello from lesson" in view.text
    assert "inline" in view.headers.get("content-disposition", "")

    download = client.get(
        f"/api/live/sessions/{sid}/lesson.txt?download=1"
    )
    assert download.status_code == 200
    assert "attachment" in download.headers.get("content-disposition", "")
    assert "recording.txt" in download.headers.get("content-disposition", "")


def test_stop_with_outstanding_transcription_shows_processing(api_client):
    client, service, _, core = api_client
    sid, task_id = _start_with_chunk(client, core, complete=False)

    stopped = client.post(f"/api/live/sessions/{sid}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "processing"

    active = client.get("/api/live/sessions/active")
    assert active.json()["session"]["status"] == "processing"
    assert active.json()["session"]["session_id"] == sid

    core.complete(task_id, "later text")
    final = client.post(f"/api/live/sessions/{sid}/poll")
    assert final.status_code == 200
    assert final.json()["status"] == "completed"
    assert final.json()["has_lesson_txt"] is True

    active_after = client.get("/api/live/sessions/active")
    assert active_after.json()["session"] is None

    detail = client.get(f"/api/live/sessions/{sid}")
    assert detail.json()["status"] == "completed"


def test_stop_without_extra_chunk_is_ok(api_client):
    client, _, _, core = api_client
    sid, _ = _start_with_chunk(client, core, complete=True)
    # Already transcribed; stop should complete without requiring a new chunk.
    stopped = client.post(f"/api/live/sessions/{sid}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "completed"


def test_delete_completed_lesson(api_client):
    client, service, _, core = api_client
    sid, _ = _start_with_chunk(client, core, complete=True)
    client.post(f"/api/live/sessions/{sid}/stop")

    session_dir = service.store.session_dir(sid)
    assert session_dir.is_dir()
    assert (session_dir / "session.json").is_file()
    assert (session_dir / "lesson.txt").is_file()
    assert (session_dir / "audio").is_dir()
    assert (session_dir / "transcripts").is_dir()

    deleted = client.delete(f"/api/live/sessions/{sid}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    assert not session_dir.exists()

    missing = client.get(f"/api/live/sessions/{sid}")
    assert missing.status_code == 404

    listed = client.get("/api/live/sessions?limit=10")
    assert all(item["session_id"] != sid for item in listed.json()["sessions"])


def test_delete_cancelled_and_failed_allowed(api_client, tmp_path: Path):
    client, service, _, _ = api_client

    started = client.post(
        "/api/live/sessions/start",
        json={"title": "Cancel me", "language": "auto"},
    )
    sid = started.json()["session_id"]
    cancelled = client.post(f"/api/live/sessions/{sid}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    assert client.delete(f"/api/live/sessions/{sid}").status_code == 200
    assert not service.store.session_dir(sid).exists()

    # Create a failed session via direct store save.
    from app.lessons.models import LessonSession, SessionStatus, utc_now_iso

    failed = LessonSession.create(title="Failed", language="auto")
    failed.status = SessionStatus.FAILED
    failed.ended_at = utc_now_iso()
    failed.error = "boom"
    service.store.save(failed)
    assert client.delete(
        f"/api/live/sessions/{failed.session_id}"
    ).status_code == 200


def test_delete_path_traversal_blocked(api_client):
    client, service, _, _ = api_client
    for bad_id in ("../escape", "..", "a/b", "a\\b"):
        response = client.delete(f"/api/live/sessions/{bad_id}")
        assert response.status_code in {400, 404, 422}

    import pytest

    for bad_id in ("../escape", "..", "a/b", "a\\b", "", "."):
        with pytest.raises(ValueError):
            service.store.delete_session(bad_id)


def test_cannot_delete_in_progress_lesson(api_client):
    client, _, _, _ = api_client
    started = client.post(
        "/api/live/sessions/start",
        json={"title": "Busy", "language": "auto"},
    )
    sid = started.json()["session_id"]
    denied = client.delete(f"/api/live/sessions/{sid}")
    assert denied.status_code == 409


def test_index_keeps_completed_lesson_in_ui_contract(api_client):
    """UI keeps Recent Recordings compact and active panel simple."""
    client, _, _, _ = api_client
    html = client.get("/").text
    assert "Recent Recordings" in html
    assert "LIVE RECORDING" in html
    assert "live-recent-list" in html
    assert "live-wave" in html
    assert "Записано:" in html
    assert "Пауза:" in html
    assert "Cancel the current recording?" not in html
    assert "Скасувати поточний запис?" in html
    assert ">Elapsed<" not in html
    assert ">Chunks<" not in html
    assert "clearOnStop" in html
    assert "/live/recordings/" in html
    assert "Скасувати поточний урок?" not in html


def test_live_actions_are_not_blocked_by_secondary_refreshes(api_client):
    client, _, _, _ = api_client
    html = client.get("/").text

    assert "Secondary refreshes must never keep action buttons disabled" in html
    assert "const controller = new AbortController()" in html
    assert "liveInFlight = false" in html
    assert "void refreshRecentRecordings()" in html


@pytest.mark.asyncio
async def test_pause_exposes_current_pause_timer_fields(lesson_env):
    service, _, _, _, _ = lesson_env
    session = await service.start_lesson(title="Timer", language="auto")
    sid = session["session_id"]
    paused = await service.pause_lesson(sid)
    assert paused["status"] == "paused"
    assert paused["pause_started_at"]
    assert paused["captured_duration_seconds"] == paused["captured_duration_seconds"]
    assert "current_pause_seconds" in paused
    assert paused["current_pause_seconds"] >= 0

    # Captured stays stable across pause refreshes while wall may grow.
    captured = paused["captured_duration_seconds"]
    import asyncio
    await asyncio.sleep(0.05)
    again = await service.get_session(sid)
    assert again["status"] == "paused"
    assert again["captured_duration_seconds"] == captured
    assert again["wall_duration_seconds"] >= paused["wall_duration_seconds"]

    resumed = await service.resume_lesson(sid)
    assert resumed["status"] == "recording"
    assert resumed["pause_started_at"] is None
    assert resumed["current_pause_seconds"] == 0


@pytest.mark.asyncio
async def test_completed_session_exposes_duration_breakdown(lesson_env):
    service, store, _, core, _ = lesson_env
    session = await service.start_lesson(title="Done", language="auto")
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
    core.complete(task_id, "text")
    await service.poll_session_transcriptions(sid)
    final = await service.stop_lesson(sid)
    assert final["status"] == "completed"
    assert "wall_duration_seconds" in final
    assert final["captured_duration_seconds"] == 30
    assert "paused_duration_seconds" in final


def test_completion_visible_via_poll_without_reload(api_client):
    client, _, _, core = api_client
    sid, task_id = _start_with_chunk(client, core, complete=False)
    stopped = client.post(f"/api/live/sessions/{sid}/stop")
    assert stopped.json()["status"] == "processing"

    core.complete(task_id, "async complete")
    polled = client.post(f"/api/live/sessions/{sid}/poll")
    assert polled.json()["status"] == "completed"
    assert polled.json()["has_lesson_txt"] is True
    detail = client.get(f"/api/live/sessions/{sid}")
    assert detail.json()["session_id"] == sid
    assert detail.json()["status"] == "completed"
