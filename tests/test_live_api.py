from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_live_lesson import FakeCapture, FakeCore, make_wav


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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

    # Reload settings-dependent globals used by routes.
    from app.lessons import config as config_module
    from app.lessons import routes as routes_module
    from app.lessons import service as service_module
    from app.lessons.config import LiveLessonSettings
    from app.lessons.service import LessonService
    from app.lessons.storage import SessionStore

    settings = LiveLessonSettings()
    config_module.settings = settings
    routes_module.settings = settings

    capture = FakeCapture()
    core = FakeCore()
    service = LessonService(
        settings=settings,
        store=SessionStore(settings.sessions_root),
        capture_client=capture,  # type: ignore[arg-type]
        core_client=core,  # type: ignore[arg-type]
    )
    service.ensure_ready()
    service_module.lesson_service = service
    routes_module.lesson_service = service

    import app.main as main_module

    main_module.lesson_service = service
    main_module.AUDIO_ROOT = audio_root

    with TestClient(main_module.app) as client:
        yield client, service, capture, core


def test_health_and_existing_upload_still_work(api_client, tmp_path: Path):
    client, _, _, _ = api_client
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["version"] == "0.3.0"

    wav = make_wav()
    response = client.post(
        "/upload",
        files={"file": ("note.wav", wav, "audio/wav")},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_chunk_upload_requires_auth(api_client):
    client, service, _, _ = api_client
    import asyncio

    session = asyncio.run(service.start_lesson(title="Auth lesson", language="auto"))
    sid = session["session_id"]

    denied = client.post(
        f"/api/live/sessions/{sid}/chunks",
        data={
            "chunk_index": "1",
            "start_offset_seconds": "0",
            "end_offset_seconds": "20",
            "duration_seconds": "20",
        },
        files={"audio": ("chunk_0001.wav", make_wav(), "audio/wav")},
    )
    assert denied.status_code == 401

    ok = client.post(
        f"/api/live/sessions/{sid}/chunks",
        headers={"Authorization": "Bearer secret-token"},
        data={
            "chunk_index": "1",
            "start_offset_seconds": "0",
            "end_offset_seconds": "20",
            "duration_seconds": "20",
        },
        files={"audio": ("chunk_0001.wav", make_wav(), "audio/wav")},
    )
    assert ok.status_code == 200
    assert ok.json()["chunk_index"] == 1


def test_live_start_api(api_client):
    client, _, capture, _ = api_client
    response = client.post(
        "/api/live/sessions/start",
        json={"title": "API Lesson", "language": "uk"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "recording"
    assert capture.calls[0][0] == "start"
