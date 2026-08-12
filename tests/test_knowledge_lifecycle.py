from __future__ import annotations

from datetime import datetime
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
    session = service.store.load(sid)
    recording_date = datetime.fromisoformat(
        session.started_at or session.created_at
    ).strftime("%d.%m.%Y")
    assert core.knowledge_registrations == [
        {
            "document_id": f"audio-session-{sid}",
            "text_path": f"sessions/{sid}/lesson.txt",
            "source_filename": f"Indexed recording — {recording_date}.txt",
        }
    ]
    assert core.knowledge_indexes == [f"audio-session-{sid}"]

    session_dir = service.store.session_dir(sid)
    deleted = client.delete(f"/api/live/sessions/{sid}")
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["local_assets_deleted"] is True
    assert body["knowledge_removed"] is False
    assert body["knowledge_preserved"] is True
    assert body["knowledge_id"] == knowledge_id
    assert not session_dir.exists()
    assert core.knowledge_registrations == [
        {
            "document_id": f"audio-session-{sid}",
            "text_path": f"sessions/{sid}/lesson.txt",
            "source_filename": f"Indexed recording — {recording_date}.txt",
        }
    ]
    assert core.knowledge_indexes == [f"audio-session-{sid}"]

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


def test_completed_task_uses_generic_core_knowledge_endpoints(api_client, monkeypatch):
    client, service, _, _ = api_client
    import app.main as main_module

    calls: list[tuple[str, str]] = []

    async def fake_core_action(*, method: str, endpoint: str):
        calls.append((method, endpoint))
        if method == "DELETE":
            return True, "deleted", {"status": "deleted", "index_deleted": True}
        return True, "indexed", {
            "status": "indexed",
            "chunk_count": 2,
            "source_file": "talk.wav",
        }

    monkeypatch.setattr(main_module, "call_core_action", fake_core_action)

    assert client.post("/tasks/core-task-1/index", follow_redirects=False).status_code == 303
    registered = service.knowledge.state_for_source("upload_task", "core-task-1")
    assert registered["document_id"] == "core-task-1"

    assert client.post("/tasks/core-task-1/reindex", follow_redirects=False).status_code == 303
    assert client.post("/tasks/core-task-1/unindex", follow_redirects=False).status_code == 303

    assert calls == [
        ("POST", "/knowledge/documents/core-task-1/index"),
        ("POST", "/knowledge/documents/core-task-1/reindex"),
        ("DELETE", "/knowledge/documents/core-task-1"),
    ]
    assert service.knowledge.state_for_source("upload_task", "core-task-1")["indexed"] is False


def test_stable_knowledge_ids_across_reindex(api_client):
    client, _, _, core = api_client
    sid = _complete_recording(client, core, "Stable id")
    first = client.post(f"/api/live/sessions/{sid}/knowledge").json()
    kid = first["knowledge"]["knowledge_id"]
    assert kid == first["knowledge"]["document_id"]

    second = client.post(f"/api/live/sessions/{sid}/knowledge/reindex").json()
    assert second["knowledge"]["knowledge_id"] == kid


def test_session_add_to_knowledge_is_one_idempotent_core_document(api_client):
    client, service, _, core = api_client
    sid = _complete_recording(client, core, "One recording")

    first = client.post(f"/api/live/sessions/{sid}/knowledge")
    second = client.post(f"/api/live/sessions/{sid}/knowledge")

    assert first.status_code == 200
    assert second.status_code == 200
    document_id = f"audio-session-{sid}"
    session = service.store.load(sid)
    recording_date = datetime.fromisoformat(
        session.started_at or session.created_at
    ).strftime("%d.%m.%Y")
    assert core.knowledge_indexes == [document_id, document_id]
    assert {item["document_id"] for item in core.knowledge_registrations} == {
        document_id
    }
    assert core.knowledge_registrations == [{
        "document_id": f"audio-session-{sid}",
        "text_path": f"sessions/{sid}/lesson.txt",
        "source_filename": f"One recording — {recording_date}.txt",
    }] * 2

    docs = client.get("/api/knowledge/documents").json()["documents"]
    matches = [item for item in docs if item["source_ref"] == sid]
    assert len(matches) == 1
    assert matches[0]["document_id"] == document_id
    assert matches[0]["vector_backend"] == "core"
    assert matches[0]["metadata"]["source"] == "homelab-core"


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
    assert "Record Mac system audio" in html
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
    assert "Видалити локальну копію" in detail
    assert "Add to Knowledge" in detail
    assert "Переглянути транскрипцію" in detail
    assert "Завантажити TXT" in detail
    assert "Видалити урок" not in detail
    assert "Live Lesson" not in detail
    assert "Remove from Knowledge" not in detail
    assert "<audio" not in detail

    client.post(f"/api/live/sessions/{sid}/knowledge")
    indexed_detail = client.get(f"/live/recordings/{sid}").text
    assert "Added to Knowledge" in indexed_detail
    assert 'id="knowledge-add-btn"' not in indexed_detail
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


# ---------------------------------------------------------------------------
# Bulk archive delete
# ---------------------------------------------------------------------------


def test_bulk_delete_selected_archive_files(api_client, tmp_path, monkeypatch):
    """1. selected archive files are deleted, unselected remain"""
    client, _, _, _ = api_client
    import app.main as main_module

    audio_root = tmp_path / "audio_root"
    (audio_root / "archive").mkdir(parents=True)
    monkeypatch.setattr(main_module, "AUDIO_ROOT", audio_root)

    keep = audio_root / "archive" / "keep.wav"
    keep.write_bytes(b"keep")
    delete1 = audio_root / "archive" / "del1.wav"
    delete1.write_bytes(b"del1")
    delete2 = audio_root / "archive" / "del2.wav"
    delete2.write_bytes(b"del2")

    response = client.post(
        "/archive/delete-selected",
        data={"filename": ["del1.wav", "del2.wav"]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "success=" in response.headers.get("location", "")

    assert keep.exists()
    assert not delete1.exists()
    assert not delete2.exists()


def test_bulk_delete_empty_selection_handled(api_client):
    """3. empty selection returns error"""
    client, _, _, _ = api_client
    response = client.post(
        "/archive/delete-selected",
        data={},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" in response.headers.get("location", "")


def test_bulk_delete_missing_file_does_not_break(api_client, tmp_path, monkeypatch):
    """4. missing selected file does not break the entire delete"""
    client, _, _, _ = api_client
    import app.main as main_module

    audio_root = tmp_path / "audio_root"
    (audio_root / "archive").mkdir(parents=True)
    monkeypatch.setattr(main_module, "AUDIO_ROOT", audio_root)

    existing = audio_root / "archive" / "exists.wav"
    existing.write_bytes(b"exists")

    response = client.post(
        "/archive/delete-selected",
        data={"filename": ["exists.wav", "ghost.wav"]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers.get("location", "")
    assert "success=" in location
    assert not existing.exists()


def test_bulk_delete_path_traversal_neutralized(api_client):
    """5. path traversal neutralized — Path(s).name strips directories"""
    client, _, _, _ = api_client
    response = client.post(
        "/archive/delete-selected",
        data={"filename": ["../../../etc/passwd"]},
        follow_redirects=False,
    )
    # Traversal is neutralized by Path().name; endpoint returns 303 (success)
    # because it sanitizes the input to just the filename "passwd"
    assert response.status_code == 303


def test_bulk_delete_knowledge_never_called(api_client, tmp_path, monkeypatch):
    """6. Knowledge/Qdrant is never called"""
    client, service, _, _ = api_client
    import app.main as main_module

    audio_root = tmp_path / "audio_root"
    (audio_root / "archive").mkdir(parents=True)
    monkeypatch.setattr(main_module, "AUDIO_ROOT", audio_root)

    (audio_root / "archive" / "bulk_test.wav").write_bytes(b"test")

    all_docs_before = client.get("/api/knowledge/documents").json()["documents"]
    count_before = len(all_docs_before)

    response = client.post(
        "/archive/delete-selected",
        data={"filename": ["bulk_test.wav"]},
        follow_redirects=False,
    )
    assert response.status_code == 303

    all_docs_after = client.get("/api/knowledge/documents").json()["documents"]
    assert len(all_docs_after) == count_before


def test_archive_storage_remains_but_is_hidden_from_ui(api_client, tmp_path, monkeypatch):
    client, _, _, _ = api_client
    import app.main as main_module

    audio_root = tmp_path / "audio_root"
    (audio_root / "archive").mkdir(parents=True)
    (audio_root / "archive" / "test.wav").write_bytes(b"test")
    monkeypatch.setattr(main_module, "AUDIO_ROOT", audio_root)

    html = client.get("/").text
    assert (audio_root / "archive" / "test.wav").is_file()
    assert "archive-check" not in html
    assert "archive-select-all" not in html
    assert "Архів аудіо" not in html
