from __future__ import annotations

from app.lessons.models import ChunkStatus, LessonSession


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def assemble_lesson_transcript(session: LessonSession) -> str:
    """Build lesson.txt preserving chronological order and pause gaps."""
    lines: list[str] = [
        f"# {session.title}",
        f"# session_id: {session.session_id}",
        f"# language: {session.language}",
        "",
    ]

    chunks = sorted(session.chunks, key=lambda item: item.chunk_index)
    previous_end: float | None = None

    for chunk in chunks:
        if previous_end is not None:
            gap = chunk.start_offset_seconds - previous_end
            if gap >= 1.0:
                lines.append("[PAUSE / no captured audio]")
                lines.append("")

        start = format_timestamp(chunk.start_offset_seconds)
        end = format_timestamp(chunk.end_offset_seconds)
        lines.append(f"[{start} – {end}]")

        if chunk.status == ChunkStatus.TRANSCRIBED and chunk.transcript_path:
            # Text is loaded by caller via storage; placeholder filled below
            # when using assemble_from_texts.
            pass

        previous_end = chunk.end_offset_seconds

    # This function expects texts to be injected via assemble_from_texts.
    # Keep a simple fallback that only writes headers if no texts map given.
    return "\n".join(lines).rstrip() + "\n"


def assemble_from_texts(
    session: LessonSession,
    transcripts: dict[int, str],
) -> str:
    lines: list[str] = [
        f"# {session.title}",
        f"# session_id: {session.session_id}",
        f"# language: {session.language}",
        "",
    ]

    chunks = sorted(session.chunks, key=lambda item: item.chunk_index)
    previous_end: float | None = None

    for chunk in chunks:
        if chunk.status == ChunkStatus.CANCELLED:
            continue

        if previous_end is not None:
            gap = chunk.start_offset_seconds - previous_end
            if gap >= 1.0:
                lines.append("[PAUSE / no captured audio]")
                lines.append("")

        start = format_timestamp(chunk.start_offset_seconds)
        end = format_timestamp(chunk.end_offset_seconds)
        lines.append(f"[{start} – {end}]")

        text = transcripts.get(chunk.chunk_index, "").strip()
        if chunk.status == ChunkStatus.FAILED:
            lines.append(f"[transcription failed: {chunk.error or 'unknown error'}]")
        elif chunk.status in {
            ChunkStatus.UPLOADED,
            ChunkStatus.TRANSCRIBING,
            ChunkStatus.RECORDING,
        }:
            lines.append("[transcription pending]")
        elif text:
            lines.append(text)
        else:
            lines.append("[empty transcript]")

        lines.append("")
        previous_end = chunk.end_offset_seconds

    return "\n".join(lines).rstrip() + "\n"
