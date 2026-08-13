from __future__ import annotations

import asyncio
import re

from app.lessons.config import LiveLessonSettings


class CaptureProcessError(RuntimeError):
    """A server-side failure while controlling the Mac launchd job."""


class MacCaptureProcessController:
    """Controls the on-demand Capture Agent without exposing SSH to clients."""

    def __init__(self, settings: LiveLessonSettings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.mac_capture_process_control == "ssh"

    async def start_agent_process(self) -> None:
        await self._run_launchctl("kickstart", f"{self._target()}")

    async def stop_agent_process(self) -> None:
        # launchctl kill addresses the service target, not a PID.
        await self._run_launchctl("kill", "SIGTERM", self._target())

    def _target(self) -> str:
        domain = self.settings.mac_capture_launchd_domain.rstrip("/")
        label = self.settings.mac_capture_launchd_label
        if not domain or not label:
            raise CaptureProcessError(
                "Mac Capture process control is not fully configured"
            )
        if not re.fullmatch(r"gui/[0-9]+", domain):
            raise CaptureProcessError("Invalid MAC_CAPTURE_LAUNCHD_DOMAIN")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", label):
            raise CaptureProcessError("Invalid MAC_CAPTURE_LAUNCHD_LABEL")
        return f"{domain}/{label}"

    async def _run_launchctl(self, *launchctl_args: str) -> None:
        if not self.enabled:
            return
        host = self.settings.mac_capture_ssh_host
        user = self.settings.mac_capture_ssh_user
        if not host or not user:
            raise CaptureProcessError(
                "Mac Capture SSH host and user must be configured"
            )

        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self.settings.mac_capture_ssh_connect_timeout_seconds}",
            f"{user}@{host}",
            "launchctl",
            *launchctl_args,
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
        except OSError as exc:
            raise CaptureProcessError("Mac unavailable") from exc

        if process.returncode:
            detail = stderr.decode("utf-8", errors="replace").strip()
            if "Permission denied" in detail or "Connection" in detail:
                raise CaptureProcessError("Mac unavailable") from None
            raise CaptureProcessError("Could not start Mac Capture Agent") from None
