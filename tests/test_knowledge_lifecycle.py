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


def test_remove_from_knowledge_api_only(api_client):
    """Knowledge removal stays on API layer, not in Audio Lab UI."""
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


def test_stable_knowledge_ids_across_reindex(api_client):
    client, _, _, core = api_client
    sid = _complete_recording(client, core, "Stable id")
    first = client.post(f"/api/live/sessions/{sid}/knowledge").json()
    kid = first["knowledge"]["knowledge_id"]
    assert kid == first["knowledge"]["document_id"]

    second = client.post(f"/api/live/sessions/{sid}/knowledge/reindex").json()
    assert second["knowledge"]["knowledge_id"] == kid


def test_knowledge_survives_local_deletion(api_client):
    client, _, _, core = api_client
    sid = _complete_recording(client, core, "Orphan knowledge")
    kid = client.post(f"/api/live/sessions/{sid}/knowledge").json()["knowledge"][
        "knowledge_id"
    ]
    client.delete(f"/api/live/sessions/{sid}")

    docs = client.get("/api/knowledge/documents").json()["documents"]
    assert any(d["knowledge_id"] == kid and not d["local_assets_present"] for d in docs)


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


def test_ui_uses_recording_terminology_not_lesson(api_client):
    client, _, _, core = api_client
    html = client.get("/").text
    assert "LIVE RECORDING" in html
    assert "Recent Recordings" in html
    assert "Назва запису" in html
    assert "Почати запис" in html
    assert "LIVE LESSON" not in html
    assert "Recent Lessons" not in html
    assert "Start Lesson" not in html
    assert "Додати в Qdrant" not in html
    assert "Видалити з Qdrant" not in html
    assert "knowledge-documents" not in html
    assert "Remove from Knowledge" not in html

    sid = _complete_recording(client, core, "Detail terms")
    detail = client.get(f"/live/recordings/{sid}").text
    assert "Видалити запис" in detail
    assert "Додати в базу знань" in detail
    assert "Переглянути транскрипцію" in detail
    assert "Завантажити TXT" in detail
    assert "Видалити урок" not in detail
    assert "Live Lesson" not in detail
    assert "Remove from Knowledge" not in detail
    assert "<audio" in detail

    client.post(f"/api/live/sessions/{sid}/knowledge")
    indexed_detail = client.get(f"/live/recordings/{sid}").text
    assert "База знань: Додано ✓" in indexed_detail
    assert "Reindex" not in indexed_detail
    assert "Remove from Knowledge" not in indexed_detail


# ---------------------------------------------------------------------------
# Local delete for completed transcriptions (Завершені транскрипції)
# ---------------------------------------------------------------------------


def _audio_tasks_returning(*completed_tasks):
    """Fake audio_tasks() that returns the given completed tasks."""

    async def _fake():
        return {"tasks": list(completed_tasks), "total": len(completed_tasks)}

    return _fake


def test_local_delete_all_files_exist_succeeds(api_client, tmp_path, monkeypatch):
    """1. all files exist -> delete succeeds and item disappears"""
    client, service, _, _ = api_client
    import app.main as main_module

    audio_root = tmp_path / "audio_root"
    (audio_root / "archive").mkdir(parents=True)
    (audio_root / "ready").mkdir(parents=True)
    monkeypatch.setattr(main_module, "AUDIO_ROOT", audio_root)
    monkeypatch.setattr(main_module, "_LOCAL_DELETED_TASKS_PATH", audio_root / ".local_deleted_tasks.txt")

    archive_file = audio_root / "archive" / "talk.mp3"
    archive_file.write_bytes(b"fake-mp3")
    txt_file = audio_root / "ready" / "talk.txt"
    txt_file.write_text("hello world", encoding="utf-8")
    json_file = audio_root / "ready" / "talk.json"
    json_file.write_text('{"text":"hello world"}', encoding="utf-8")

    fake_task = {
        "id": "task-local-1",
        "status": "completed",
        "source_file": "talk.mp3",
        "result": {"output_files": ["ready/talk.txt", "ready/talk.json"]},
        "index": {"index_status": "not_indexed"},
    }

    monkeypatch.setattr(main_module, "audio_tasks", _audio_tasks_returning(fake_task))

    response = client.post("/tasks/task-local-1/local-delete", follow_redirects=False)
    assert response.status_code == 303
    location = response.headers.get("location", "")
    assert "success=" in location

    assert not archive_file.exists()
    assert not txt_file.exists()
    assert not json_file.exists()

    # Verify task is now hidden from completed list
    monkeypatch.setattr(main_module, "audio_tasks", _audio_tasks_returning(fake_task))
    html = client.get("/").text
    assert "task-local-1" not in html or "Видалити локально" not in html


def test_local_delete_audio_missing_txt_json_exist(api_client, tmp_path, monkeypatch):
    """2. audio missing, TXT/JSON exist -> succeeds"""
    client, service, _, _ = api_client
    import app.main as main_module

    audio_root = tmp_path / "audio_root"
    (audio_root / "archive").mkdir(parents=True)
    (audio_root / "ready").mkdir(parents=True)
    monkeypatch.setattr(main_module, "AUDIO_ROOT", audio_root)
    monkeypatch.setattr(main_module, "_LOCAL_DELETED_TASKS_PATH", audio_root / ".local_deleted_tasks.txt")

    # NO archive file — simulating already-gone audio
    txt_file = audio_root / "ready" / "only_outputs.txt"
    txt_file.write_text("some text", encoding="utf-8")
    json_file = audio_root / "ready" / "only_outputs.json"
    json_file.write_text('{}', encoding="utf-8")

    fake_task = {
        "id": "task-audio-gone",
        "status": "completed",
        "source_file": "deleted_audio.mp3",
        "result": {"output_files": ["ready/only_outputs.txt", "ready/only_outputs.json"]},
        "index": {"index_status": "not_indexed"},
    }

    monkeypatch.setattr(main_module, "audio_tasks", _audio_tasks_returning(fake_task))

    response = client.post("/tasks/task-audio-gone/local-delete", follow_redirects=False)
    assert response.status_code == 303
    location = response.headers.get("location", "")
    assert "success=" in location
    assert not txt_file.exists()
    assert not json_file.exists()


def test_local_delete_audio_exists_txt_missing(api_client, tmp_path, monkeypatch):
    """3. audio exists, TXT missing -> succeeds"""
    client, service, _, _ = api_client
    import app.main as main_module

    audio_root = tmp_path / "audio_root"
    (audio_root / "archive").mkdir(parents=True)
    (audio_root / "ready").mkdir(parents=True)
    monkeypatch.setattr(main_module, "AUDIO_ROOT", audio_root)
    monkeypatch.setattr(main_module, "_LOCAL_DELETED_TASKS_PATH", audio_root / ".local_deleted_tasks.txt")

    archive_file = audio_root / "archive" / "has_audio.mp3"
    archive_file.write_bytes(b"fake-mp3")

    fake_task = {
        "id": "task-txt-gone",
        "status": "completed",
        "source_file": "has_audio.mp3",
        "result": {"output_files": ["ready/missing.txt", "ready/missing.json"]},
        "index": {"index_status": "not_indexed"},
    }

    monkeypatch.setattr(main_module, "audio_tasks", _audio_tasks_returning(fake_task))

    response = client.post("/tasks/task-txt-gone/local-delete", follow_redirects=False)
    assert response.status_code == 303
    location = response.headers.get("location", "")
    assert "success=" in location
    assert not archive_file.exists()


def test_local_delete_all_files_missing_succeeds(api_client, tmp_path, monkeypatch):
    """4. all local files missing -> still succeeds and item disappears"""
    client, service, _, _ = api_client
    import app.main as main_module

    audio_root = tmp_path / "audio_root"
    (audio_root / "archive").mkdir(parents=True)
    (audio_root / "ready").mkdir(parents=True)
    monkeypatch.setattr(main_module, "AUDIO_ROOT", audio_root)
    monkeypatch.setattr(main_module, "_LOCAL_DELETED_TASKS_PATH", audio_root / ".local_deleted_tasks.txt")

    fake_task = {
        "id": "task-all-gone",
        "status": "completed",
        "source_file": "ghost.mp3",
        "result": {"output_files": ["ready/ghost.txt", "ready/ghost.json"]},
        "index": {"index_status": "not_indexed"},
    }

    monkeypatch.setattr(main_module, "audio_tasks", _audio_tasks_returning(fake_task))

    response = client.post("/tasks/task-all-gone/local-delete", follow_redirects=False)
    assert response.status_code == 303
    location = response.headers.get("location", "")
    assert "success=" in location
    assert "%D0%B2%D0%B6%D0%B5" in location

    # Item disappears from completed tasks
    monkeypatch.setattr(main_module, "audio_tasks", _audio_tasks_returning(fake_task))
    html = client.get("/").text
    assert "task-all-gone" not in html or "Видалити локально" not in html


def test_local_delete_indexed_preserves_knowledge(api_client, tmp_path, monkeypatch):
    """5. indexed item remains in Knowledge after local delete"""
    client, service, _, _ = api_client
    import app.main as main_module

    audio_root = tmp_path / "audio_root"
    (audio_root / "archive").mkdir(parents=True)
    (audio_root / "ready").mkdir(parents=True)
    monkeypatch.setattr(main_module, "AUDIO_ROOT", audio_root)
    monkeypatch.setattr(main_module, "_LOCAL_DELETED_TASKS_PATH", audio_root / ".local_deleted_tasks.txt")

    archive_file = audio_root / "archive" / "indexed_talk.mp3"
    archive_file.write_bytes(b"fake-mp3")
    txt_file = audio_root / "ready" / "indexed_talk.txt"
    txt_file.write_text("indexed hello", encoding="utf-8")

    registered = service.knowledge.register_upload_task(
        task_id="task-local-2",
        title="indexed_talk.mp3",
        source_file="indexed_talk.mp3",
        chunk_count=5,
    )
    knowledge_id = registered["knowledge_id"]

    fake_task = {
        "id": "task-local-2",
        "status": "completed",
        "source_file": "indexed_talk.mp3",
        "result": {"output_files": ["ready/indexed_talk.txt"]},
        "index": {"index_status": "indexed"},
    }

    monkeypatch.setattr(main_module, "audio_tasks", _audio_tasks_returning(fake_task))

    response = client.post("/tasks/task-local-2/local-delete", follow_redirects=False)
    assert response.status_code == 303
    assert not archive_file.exists()
    assert not txt_file.exists()

    docs = client.get("/api/knowledge/documents").json()["documents"]
    match = next(d for d in docs if d["knowledge_id"] == knowledge_id)
    assert match["local_assets_present"] is False
    assert match["indexed"] is True


def test_local_delete_path_traversal_rejected(api_client, monkeypatch):
    """6. path traversal still rejected"""
    client, _, _, _ = api_client
    import app.main as main_module

    fake_task = {
        "id": "task-bad-1",
        "status": "completed",
        "source_file": "../../../etc/passwd",
        "result": {"output_files": ["ready/../../../etc/shadow"]},
        "index": {"index_status": "not_indexed"},
    }

    monkeypatch.setattr(main_module, "audio_tasks", _audio_tasks_returning(fake_task))

    response = client.post("/tasks/task-bad-1/local-delete", follow_redirects=False)
    assert response.status_code == 303
    # safe_path rejects traversal; endpoint doesn't crash
    assert "/etc/passwd" not in response.headers.get("location", "")


def test_local_delete_knowledge_never_indexed(api_client, tmp_path, monkeypatch):
    """Verify local-delete never calls Knowledge indexing APIs."""
    client, service, _, _ = api_client
    import app.main as main_module

    audio_root = tmp_path / "audio_root"
    (audio_root / "archive").mkdir(parents=True)
    (audio_root / "ready").mkdir(parents=True)
    monkeypatch.setattr(main_module, "AUDIO_ROOT", audio_root)
    monkeypatch.setattr(main_module, "_LOCAL_DELETED_TASKS_PATH", audio_root / ".local_deleted_tasks.txt")

    archive_file = audio_root / "archive" / "no_index.mp3"
    archive_file.write_bytes(b"fake-mp3")

    fake_task = {
        "id": "task-local-3",
        "status": "completed",
        "source_file": "no_index.mp3",
        "result": {"output_files": []},
        "index": {"index_status": "not_indexed"},
    }

    monkeypatch.setattr(main_module, "audio_tasks", _audio_tasks_returning(fake_task))

    all_docs_before = client.get("/api/knowledge/documents").json()["documents"]
    count_before = len(all_docs_before)

    response = client.post("/tasks/task-local-3/local-delete", follow_redirects=False)
    assert response.status_code == 303
    assert not archive_file.exists()

    all_docs_after = client.get("/api/knowledge/documents").json()["documents"]
    assert len(all_docs_after) == count_before
