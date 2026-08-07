# Live Recording Mode

Additive feature for Audio Lab: capture macOS **system audio only**, chunk live, transcribe via Homelab Core while recording continues.

Audio Lab is a generic **Audio Knowledge Capture** system (lectures, podcasts, meetings, voice notes — not lesson-specific).

## Two independent layers

**Layer A — Local assets**

- `session.json`, audio chunks, transcripts, assembled `lesson.txt` (internal filename)

**Layer B — Knowledge**

- Stable `knowledge_id` / `document_id`
- Knowledge registry + (when wired) Qdrant vectors

Deletion never cascades between layers:

- **Delete Recording** → local assets only; Knowledge remains
- **Remove from Knowledge** → Knowledge only; local audio/transcripts remain

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
- Knowledge registry: `KNOWLEDGE_REGISTRY_PATH` (default `/remote/Audio/knowledge/registry.json`)

### Session layout

```text
sessions/<session_id>/
  session.json
  lesson.txt          # assembled recording transcript (compat filename)
  audio/chunk_0001.wav
  transcripts/chunk_0001.txt
```

Chunk audio is **copied** into `/remote/Audio/processing/` before Core enqueue because Homelab Core archives (moves) `file_path` after transcription. Canonical recording audio stays under `sessions/`.

## Extension points (not implemented yet)

- Full Core/Qdrant vector sync for live recordings (local Knowledge registry is live)
- LLM summary / notes
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

On the Mac that plays system audio:

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
```

## UI flow

1. Start Mac capture agent; Audio Lab **LIVE RECORDING** shows Capture Agent online.
2. Set **Recording title** + language → **Start Recording**.
3. Pause / Resume / Stop as needed.
4. After Stop, recording appears under **Recent Recordings** (Processing → Completed).
5. Open detail: listen to chunks, view/download transcript, Add to Knowledge, or Delete Recording.
