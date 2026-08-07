from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


class LiveLessonSettings:
    def __init__(self) -> None:
        audio_root = Path(
            _env("AUDIO_ROOT", "/remote/Audio")
        ).resolve()

        sessions_root = _env(
            "LIVE_SESSIONS_ROOT",
            str(audio_root / "sessions"),
        )

        self.audio_root = audio_root
        self.sessions_root = Path(sessions_root).resolve()
        self.core_url = _env(
            "CORE_URL",
            "http://ai-gateway:8080",
        ).rstrip("/")
        self.mac_capture_agent_url = _env(
            "MAC_CAPTURE_AGENT_URL",
            "http://127.0.0.1:8011",
        ).rstrip("/")
        self.mac_capture_agent_token = _env(
            "MAC_CAPTURE_AGENT_TOKEN",
            "",
        )
        self.live_upload_token = _env(
            "LIVE_UPLOAD_TOKEN",
            "",
        ) or self.mac_capture_agent_token
        self.audio_lab_public_url = _env(
            "AUDIO_LAB_PUBLIC_URL",
            "http://127.0.0.1:3011",
        ).rstrip("/")
        self.chunk_silence_seconds = _env_float(
            "LIVE_CHUNK_SILENCE_SECONDS",
            5.0,
        )
        self.chunk_min_seconds = _env_float(
            "LIVE_CHUNK_MIN_SECONDS",
            20.0,
        )
        self.chunk_max_seconds = _env_float(
            "LIVE_CHUNK_MAX_SECONDS",
            180.0,
        )
        self.max_chunk_bytes = _env_int(
            "LIVE_MAX_CHUNK_BYTES",
            30 * 1024 * 1024,
        )
        self.poll_interval_seconds = _env_float(
            "LIVE_POLL_INTERVAL_SECONDS",
            2.0,
        )


settings = LiveLessonSettings()
