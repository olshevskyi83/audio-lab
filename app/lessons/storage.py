from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from app.lessons.models import LessonSession


class SessionStore:
    """Filesystem-backed live recording session persistence."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def session_dir(self, session_id: str) -> Path:
        safe_id = Path(session_id).name
        if safe_id != session_id or not safe_id:
            raise ValueError("Invalid session_id")
        path = (self.root / safe_id).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("Invalid session path")
        return path

    def session_json_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session.json"

    def audio_dir(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "audio"

    def transcripts_dir(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "transcripts"

    def lesson_txt_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "lesson.txt"

    def create_session_dirs(self, session_id: str) -> Path:
        directory = self.session_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        self.audio_dir(session_id).mkdir(exist_ok=True)
        self.transcripts_dir(session_id).mkdir(exist_ok=True)
        return directory

    def exists(self, session_id: str) -> bool:
        return self.session_json_path(session_id).is_file()

    def load(self, session_id: str) -> LessonSession:
        path = self.session_json_path(session_id)
        if not path.is_file():
            raise FileNotFoundError(f"Session not found: {session_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return LessonSession.model_validate(payload)

    def save(self, session: LessonSession) -> None:
        session.refresh_durations()
        directory = self.create_session_dirs(session.session_id)
        target = directory / "session.json"
        payload = session.model_dump(mode="json")
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        self._atomic_write_text(target, text)

    def list_sessions(self) -> list[LessonSession]:
        self.ensure_root()
        sessions: list[LessonSession] = []
        for path in sorted(self.root.iterdir()):
            if not path.is_dir():
                continue
            json_path = path / "session.json"
            if not json_path.is_file():
                continue
            try:
                sessions.append(self.load(path.name))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        sessions.sort(key=lambda item: item.created_at, reverse=True)
        return sessions

    def write_audio_bytes(
        self,
        session_id: str,
        filename: str,
        data: bytes,
    ) -> Path:
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name.endswith(".wav"):
            raise ValueError("Invalid audio filename")
        directory = self.audio_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = (directory / safe_name).resolve()
        if directory.resolve() not in target.parents:
            raise ValueError("Invalid audio path")
        self._atomic_write_bytes(target, data)
        return target

    def write_transcript_text(
        self,
        session_id: str,
        filename: str,
        text: str,
    ) -> Path:
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name.endswith(".txt"):
            raise ValueError("Invalid transcript filename")
        directory = self.transcripts_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = (directory / safe_name).resolve()
        if directory.resolve() not in target.parents:
            raise ValueError("Invalid transcript path")
        normalized = text if text.endswith("\n") else text + "\n"
        self._atomic_write_text(target, normalized)
        return target

    def write_lesson_text(self, session_id: str, text: str) -> Path:
        target = self.lesson_txt_path(session_id)
        normalized = text if text.endswith("\n") else text + "\n"
        self._atomic_write_text(target, normalized)
        return target

    def delete_audio_file(self, session_id: str, filename: str) -> None:
        safe_name = Path(filename).name
        path = self.audio_dir(session_id) / safe_name
        if path.is_file():
            path.unlink()

    def audio_file_path(self, session_id: str, filename: str) -> Path:
        safe_name = Path(filename).name
        if (
            not filename
            or safe_name != filename
            or "/" in filename
            or "\\" in filename
            or not safe_name.endswith(".wav")
        ):
            raise ValueError("Invalid audio filename")
        directory = self.audio_dir(session_id).resolve()
        path = (directory / safe_name).resolve()
        if directory not in path.parents and path != directory:
            raise ValueError("Invalid audio path")
        if not path.is_file():
            raise FileNotFoundError(f"Audio file not found: {filename}")
        return path

    def delete_session(self, session_id: str) -> None:
        """Recursively delete one session directory under the sessions root only."""
        safe_id = Path(session_id).name
        if (
            not session_id
            or safe_id != session_id
            or safe_id in {".", ".."}
            or "/" in session_id
            or "\\" in session_id
        ):
            raise ValueError("Invalid session_id")

        directory = self.session_dir(session_id)
        root = self.root.resolve()
        resolved = directory.resolve()

        if resolved == root or root not in resolved.parents:
            raise ValueError("Session path escapes LIVE_SESSIONS_ROOT")
        if not resolved.is_dir():
            raise FileNotFoundError(f"Session not found: {session_id}")

        shutil.rmtree(resolved)

    def has_lesson_txt(self, session_id: str) -> bool:
        return self.lesson_txt_path(session_id).is_file()

    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    @staticmethod
    def _atomic_write_bytes(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
