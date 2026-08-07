# Live Lesson Mode

Additive feature for Audio Lab: capture macOS **system audio only**, chunk live, transcribe via Homelab Core while recording continues.

## Architecture

```text
Browser UI  →  Audio Lab (session owner)  →  Mac Capture Agent :8011
                      │                              │
                      │                         ScreenCaptureKit
                      │                         (no microphone)
                      ▼
               Homelab Core POST /tasks  (direct enqueue, no folder settle)
```

- Whisper Mac Agent remains on **8010** (unchanged, separate process)
- Live Capture Agent on **8011**
- Sessions stored under `LIVE_SESSIONS_ROOT` (default `/remote/Audio/sessions/<session_id>/`)

### Session layout

```text
sessions/<session_id>/
  session.json
  lesson.txt
  audio/chunk_0001.wav
  transcripts/chunk_0001.txt
```

Chunk audio is **copied** into `/remote/Audio/processing/` before Core enqueue because Homelab Core archives (moves) `file_path` after transcription. Canonical lesson audio stays under `sessions/`.

## Extension points (not implemented yet)

- Qdrant indexing of full lessons
- LLM summary / vocabulary / grammar
- Speaker diarization
- Zoom-only application audio filter
- Microphone capture

## Server deployment

From the Audio Lab repo on the homelab host:

```bash
cd /home/homelabuser/docker/ai/audio-lab

# put secrets in a local env file (never commit)
cp .env.example .env
# edit:
#   MAC_CAPTURE_AGENT_URL=http://<mac-tailscale-or-lan-ip>:8011
#   MAC_CAPTURE_AGENT_TOKEN=<shared-secret>
#   LIVE_UPLOAD_TOKEN=<same-shared-secret>
#   AUDIO_LAB_PUBLIC_URL=http://<server-ip-or-hostname>:3011

export $(grep -v '^#' .env | xargs)

docker compose build audio-lab
docker compose up -d audio-lab
docker compose logs -f audio-lab
```

`AUDIO_LAB_PUBLIC_URL` must be reachable from the Mac (chunk upload callback).

On Linux Docker, prefer the Mac’s LAN/Tailscale IP over `host.docker.internal` for `MAC_CAPTURE_AGENT_URL`.

## Mac installation / start

On the Mac that plays Zoom/system audio:

```bash
# clone or sync this repo, then:
cd mac-capture-agent
swift build -c release

export MAC_CAPTURE_HOST=0.0.0.0
export MAC_CAPTURE_PORT=8011
export MAC_CAPTURE_AGENT_TOKEN='<same-shared-secret>'
export LIVE_CHUNK_SILENCE_SECONDS=5
export LIVE_CHUNK_MIN_SECONDS=20
export LIVE_CHUNK_MAX_SECONDS=600

./.build/release/mac-capture-agent
```

### Required macOS permission

**System Settings → Privacy & Security → Screen & System Audio Recording**

Enable access for `mac-capture-agent` (or the Terminal app if you launch via `swift run`). Restart the agent after granting permission.

## Manual acceptance test

1. Start Homelab Core + Audio Lab on the server.
2. Start `mac-capture-agent` on the Mac; confirm Audio Lab LIVE LESSON shows **Capture Agent: online**.
3. Play system audio (Zoom lesson or any speaker output).
4. In Audio Lab → **LIVE LESSON**: set title + language → **Start Lesson**.
5. Confirm status **Recording**, elapsed/captured timers move, chunks appear as silence/max boundaries hit.
6. **Pause** → status **Paused**, capture stops, current chunk finalized/uploaded.
7. Wait (pause gap) → **Resume** → same `session_id`, new chunk with larger `start_offset` (gap preserved).
8. **Stop** → status **Processing** then **Completed**.
9. Open `/api/live/sessions/<id>/lesson.txt` and confirm:
   - chronological chunk order
   - `[PAUSE / no captured audio]` between resumed segments
   - transcript text for completed chunks

## API summary

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/live/agent` | Capture agent reachability |
| GET | `/api/live/sessions/active` | Current non-terminal session |
| POST | `/api/live/sessions/start` | `{title, language}` |
| POST | `/api/live/sessions/{id}/pause\|resume\|stop\|cancel` | Controls |
| POST | `/api/live/sessions/{id}/poll` | Refresh transcription state |
| GET | `/api/live/sessions/{id}/lesson.txt` | Assembled transcript |
| POST | `/api/live/sessions/{id}/chunks` | Agent upload (Bearer token) |

## Tests

```bash
cd /home/homelabuser/docker/ai/audio-lab
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
```

Swift (on macOS, Command Line Tools / SwiftPM — full Xcode not required):

```bash
cd mac-capture-agent && swift test && swift build -c release
```
