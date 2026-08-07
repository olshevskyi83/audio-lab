from __future__ import annotations

from pathlib import Path

from tests.test_live_api import api_client  # noqa: F401
from tests.test_live_lesson import make_wav  # noqa: F401


def _complete_recording(client, core, title: str = "Talk") -> str:
    started = client.post(
        "/api/live/sessions/start",
        json={"title": title, "language": "auto"},
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
    core.complete(uploaded.json()["whisper_task_id"], "hello knowledge")
    client.post(f"/api/live/sessions/{sid}/poll")
    client.post(f"/api/live/sessions/{sid}/stop")
    return sid


def test_delete_non_indexed_recording(api_client):
    client, service, _, core = api_client
    sid = _complete_recording(client, core, "Plain recording")
    session_dir = service.store.session_dir(sid)
    assert session_dir.is_dir()

    deleted = client.delete(f"/api/live/sessions/{sid}")
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["deleted"] is True
    assert body["knowledge_removed"] is False
    assert body["knowledge_preserved"] is False
    assert not session_dir.exists()


def test_delete_indexed_recording_preserves_knowledge(api_client):
    client, service, _, core = api_client
    sid = _complete_recording(client, core, "Indexed recording")

    indexed = client.post(f"/api/live/sessions/{sid}/knowledge")
    assert indexed.status_code == 200
    knowledge = indexed.json()["knowledge"]
    assert knowledge["indexed"] is True
    knowledge_id = knowledge["knowledge_id"]
    assert knowledge_id

    session_dir = service.store.session_dir(sid)
    deleted = client.delete(f"/api/live/sessions/{sid}")
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["local_assets_deleted"] is True
    assert body["knowledge_removed"] is False
    assert body["knowledge_preserved"] is True
    assert body["knowledge_id"] == knowledge_id
    assert not session_dir.exists()

    listed = client.get("/api/knowledge/documents").json()["documents"]
    match = next(item for item in listed if item["knowledge_id"] == knowledge_id)
    assert match["local_assets_present"] is False
    assert match["indexed"] is True
    assert match["manageable_without_local"] is True


def test_remove_from_knowledge_only(api_client):
    client, service, _, core = api_client
    sid = _complete_recording(client, core, "Keep local")
    client.post(f"/api/live/sessions/{sid}/knowledge")

    removed = client.delete(f"/api/live/sessions/{sid}/knowledge")
    assert removed.status_code == 200
    assert removed.json()["knowledge"]["indexed"] is False

    session_dir = service.store.session_dir(sid)
    assert session_dir.is_dir()
    assert (session_dir / "lesson.txt").is_file()
    assert (session_dir / "audio" / "chunk_0001.wav").is_file()
    assert client.get("/api/knowledge/documents").json()["total"] == 0


def test_stable_knowledge_ids_across_reindex(api_client):
    client, _, _, core = api_client
    sid = _complete_recording(client, core, "Stable id")
    first = client.post(f"/api/live/sessions/{sid}/knowledge").json()
    kid = first["knowledge"]["knowledge_id"]
    assert kid == first["knowledge"]["document_id"]

    second = client.post(f"/api/live/sessions/{sid}/knowledge/reindex").json()
    assert second["knowledge"]["knowledge_id"] == kid

    third = client.post(f"/api/live/sessions/{sid}/knowledge").json()
    assert third["knowledge"]["knowledge_id"] == kid


def test_knowledge_only_objects_remain_manageable(api_client):
    client, _, _, core = api_client
    sid = _complete_recording(client, core, "Orphan knowledge")
    kid = client.post(f"/api/live/sessions/{sid}/knowledge").json()["knowledge"][
        "knowledge_id"
    ]
    client.delete(f"/api/live/sessions/{sid}")

    docs = client.get("/api/knowledge/documents").json()["documents"]
    assert any(d["knowledge_id"] == kid and not d["local_assets_present"] for d in docs)

    removed = client.delete(f"/api/knowledge/documents/{kid}")
    assert removed.status_code == 200
    assert removed.json()["local_assets_preserved"] is True
    assert client.get("/api/knowledge/documents").json()["total"] == 0


def test_delete_uploaded_indexed_audio_preserves_knowledge(api_client, tmp_path: Path):
    client, service, _, _ = api_client
    audio_root = service.settings.audio_root
    archive = audio_root / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    filename = "interview.wav"
    (archive / filename).write_bytes(make_wav())

    registered = service.knowledge.register_upload_task(
        task_id="task-upload-1",
        title=filename,
        source_file=filename,
        chunk_count=3,
    )
    knowledge_id = registered["knowledge_id"]

    response = client.post(
        f"/delete/archive/{filename}",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert not (archive / filename).exists()

    docs = client.get("/api/knowledge/documents").json()["documents"]
    match = next(item for item in docs if item["knowledge_id"] == knowledge_id)
    assert match["local_assets_present"] is False
    assert match["indexed"] is True


def test_ui_terminology_uses_recording(api_client):
    client, _, _, core = api_client
    html = client.get("/").text
    assert "LIVE RECORDING" in html
    assert "Recent Recordings" in html
    assert "Start Recording" in html
    assert "Recording title" in html
    assert "LIVE LESSON" not in html
    assert "Recent Lessons" not in html
    assert "Start Lesson" not in html
    assert "Add to Knowledge" in html or "Knowledge Base" in html
    assert "Remove from Knowledge" in html
    assert "Додати в Qdrant" not in html
    assert "Видалити з Qdrant" not in html

    sid = _complete_recording(client, core, "Detail terms")
    detail = client.get(f"/live/recordings/{sid}").text
    assert "Delete Recording" in detail
    assert "Add to Knowledge" in detail
    assert "Recording transcript" in detail
    assert "Видалити урок" not in detail
    assert "Live Lesson" not in detail

    client.post(f"/api/live/sessions/{sid}/knowledge")
    indexed_detail = client.get(f"/live/recordings/{sid}").text
    assert "Knowledge Base ✓" in indexed_detail
    assert "Reindex" in indexed_detail
    assert "Remove from Knowledge" in indexed_detail
    assert "Delete Recording" in indexed_detail
