from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_live_api import api_client  # noqa: F401
from tests.test_live_lesson import FakeCapture, FakeCore, make_wav


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
    assert "lesson.txt" in download.headers.get("content-disposition", "")


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

    deleted = client.delete(f"/api/live/sessions/{sid}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    missing = client.get(f"/api/live/sessions/{sid}")
    assert missing.status_code == 404

    listed = client.get("/api/live/sessions?limit=10")
    assert all(item["session_id"] != sid for item in listed.json()["sessions"])


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
    """UI must keep focused terminal sessions and list recent lessons."""
    client, _, _, _ = api_client
    html = client.get("/").text
    assert "Recent lessons" in html
    assert "live-recent-list" in html
    assert "View transcript" in html
    assert "Download lesson.txt" in html
    assert "Delete lesson" in html
    # Critical: do not clear panel just because /active is null.
    assert "isLiveTerminal(liveSession.status)" in html
    assert "/api/live/sessions?limit=10" in html


def test_completion_visible_via_poll_without_reload(api_client):
    client, _, _, core = api_client
    sid, task_id = _start_with_chunk(client, core, complete=False)
    stopped = client.post(f"/api/live/sessions/{sid}/stop")
    assert stopped.json()["status"] == "processing"

    core.complete(task_id, "async complete")
    polled = client.post(f"/api/live/sessions/{sid}/poll")
    assert polled.json()["status"] == "completed"
    assert polled.json()["has_lesson_txt"] is True
    # Same session id remains addressable for the focused panel.
    detail = client.get(f"/api/live/sessions/{sid}")
    assert detail.json()["session_id"] == sid
    assert detail.json()["status"] == "completed"
