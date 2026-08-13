# Mac Capture Agent

Local macOS system-audio capture service for Audio Lab **Live Recording** mode.

- Captures **system output only** via ScreenCaptureKit (no microphone)
- Silence / min / max chunking
- Uploads WAV chunks (`mono`, `16 kHz`, PCM16) to Audio Lab
- Separate from the Whisper Mac Agent (`8010`)
- Pure **SwiftPM** package (Command Line Tools only — full Xcode not required)

Default listen port: **8011**

## Requirements

- macOS 14+ (Sonoma or newer recommended)
- Apple Swift toolchain via **Command Line Tools** (Swift 6.x)
  - Active developer directory may be `/Library/Developer/CommandLineTools`
  - Full Xcode / `xcodebuild` is **not** required
- Permission: **System Settings → Privacy & Security → Screen & System Audio Recording**
  - Enable for `mac-capture-agent` (or your Terminal if launching from there)

## Build

```bash
cd mac-capture-agent
swift build -c release
```

Binary:

```bash
.build/release/mac-capture-agent
```

## Configure

```bash
export MAC_CAPTURE_HOST=0.0.0.0
export MAC_CAPTURE_PORT=8011
export MAC_CAPTURE_AGENT_TOKEN='your-shared-token'
export LIVE_CHUNK_SILENCE_SECONDS=5
export LIVE_CHUNK_MIN_SECONDS=20
export LIVE_CHUNK_MAX_SECONDS=600
```

Use the **same token** as Audio Lab `MAC_CAPTURE_AGENT_TOKEN` / `LIVE_UPLOAD_TOKEN`.

## Run

```bash
cd mac-capture-agent
MAC_CAPTURE_AGENT_TOKEN='your-shared-token' \
MAC_CAPTURE_PORT=8011 \
swift run -c release mac-capture-agent
```

Or:

```bash
MAC_CAPTURE_AGENT_TOKEN='your-shared-token' \
./.build/release/mac-capture-agent
```

Ensure the Audio Lab server can reach this Mac over LAN/Tailscale at `http://<mac-ip>:8011`.

For an on-demand user LaunchAgent setup, including secret handling and exact manual
install/control/uninstall commands, see [docs/launchd.md](docs/launchd.md).

On Audio Lab:

```bash
MAC_CAPTURE_AGENT_URL=http://<mac-ip>:8011
MAC_CAPTURE_AGENT_TOKEN=your-shared-token
AUDIO_LAB_PUBLIC_URL=http://<audio-lab-host>:3011
LIVE_UPLOAD_TOKEN=your-shared-token
```

`AUDIO_LAB_PUBLIC_URL` must be reachable from the Mac (chunk upload target).

## API

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health` | no | Liveness |
| GET | `/status` | yes | Current capture state |
| POST | `/sessions/start` | yes | Start system-audio capture |
| POST | `/sessions/{id}/pause` | yes | Finalize open chunk + stop capture |
| POST | `/sessions/{id}/resume` | yes | Continue same session |
| POST | `/sessions/{id}/stop` | yes | Finalize + idle |
| POST | `/sessions/{id}/cancel` | yes | Abort without upload of buffer |

## Tests (non-capture logic)

Uses **Swift Testing** (`import Testing`) — no XCTest / no Xcode project:

```bash
cd mac-capture-agent
swift test
swift build -c release
```

ScreenCaptureKit itself only runs on macOS hardware with permission granted. The `CaptureAgentCore` unit tests cover silence detection, timeline offsets, and WAV headers without opening a capture stream.

## ScreenCaptureKit / SDK note

System audio capture uses ScreenCaptureKit APIs available through the macOS SDK bundled with Command Line Tools (`SCStream`, `capturesAudio`, etc.). Optional `captureMicrophone = false` is gated with `#available(macOS 15.0, *)` and is not required for system-audio-only capture. If a future API ever requires the full Xcode SDK, that will be called out explicitly — current code is intended for CLI tools only.

## Swift 6 concurrency

- `AgentHTTPServer` serializes Network callbacks on one `DispatchQueue` and is `@unchecked Sendable` for that reason
- `SessionController` guards all mutable session state with `NSLock` (`@unchecked Sendable`)
- ScreenCaptureKit is imported with `@preconcurrency` (ObjC types lack full Sendable annotations)
- Capture start/stop uses async ScreenCaptureKit APIs bridged via a locked result box (no mutable locals in `@Sendable` completions)
- Chunk upload uses `URLSession.shared.data(for:)` async API

## Notes

- Whole system output mix for MVP (no Zoom-only filter yet)
- Empty / silence-only buffers are never uploaded
- Pause/Stop immediately close the current non-empty chunk
