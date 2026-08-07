import Foundation
import CaptureAgentCore

struct StartSessionRequest: Codable, Sendable {
    var session_id: String
    var title: String?
    var language: String?
    var started_at: String
    var upload_url: String
    var chunk_silence_seconds: Double?
    var chunk_min_seconds: Double?
    var chunk_max_seconds: Double?
}

enum AgentSessionState: String, Codable, Sendable {
    case idle
    case recording
    case paused
    case stopping
    case cancelled
}

/// Owns live-lesson capture session state.
///
/// `@unchecked Sendable` because every mutable field is accessed only while
/// holding `lock` (including audio ingest from ScreenCaptureKit callbacks and
/// upload-failure status updates from background tasks).
final class SessionController: @unchecked Sendable {
    private let lock = NSLock()
    private let config: AgentConfig
    private var state: AgentSessionState = .idle
    private var sessionId: String?
    private var uploadURL: URL?
    private var lessonStartedAt: Date?
    private var nextChunkIndex = 1
    private var chunkStartOffset: Double = 0
    private var sampleBuffer: [Float] = []
    private var detector = SilenceDetector()
    private var capture: SystemAudioCapture?
    private var authToken: String
    private var lastError: String?

    init(config: AgentConfig) {
        self.config = config
        self.authToken = config.token
        self.detector = SilenceDetector(
            rmsThreshold: config.silenceRmsThreshold,
            silenceSeconds: config.silenceSeconds,
            minChunkSeconds: config.minChunkSeconds,
            maxChunkSeconds: config.maxChunkSeconds,
            sampleRate: config.sampleRate
        )
    }

    func statusPayload() -> [String: Any] {
        lock.lock()
        defer { lock.unlock() }
        return [
            "state": state.rawValue,
            "session_id": sessionId as Any,
            "next_chunk_index": nextChunkIndex,
            "buffered_seconds": Double(sampleBuffer.count) / Double(config.sampleRate),
            "error": lastError as Any,
        ]
    }

    func start(request: StartSessionRequest) throws {
        lock.lock()
        defer { lock.unlock() }

        guard state == .idle || state == .cancelled else {
            throw AgentHTTPError(
                status: 409,
                detail: "Capture agent already has an active session"
            )
        }

        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var started = formatter.date(from: request.started_at)
        if started == nil {
            formatter.formatOptions = [.withInternetDateTime]
            started = formatter.date(from: request.started_at)
        }
        guard let startedAt = started else {
            throw AgentHTTPError(status: 400, detail: "Invalid started_at")
        }
        guard let upload = URL(string: request.upload_url) else {
            throw AgentHTTPError(status: 400, detail: "Invalid upload_url")
        }

        sessionId = request.session_id
        uploadURL = upload
        lessonStartedAt = startedAt
        nextChunkIndex = 1
        chunkStartOffset = 0
        sampleBuffer.removeAll(keepingCapacity: true)
        lastError = nil

        detector = SilenceDetector(
            rmsThreshold: config.silenceRmsThreshold,
            silenceSeconds: request.chunk_silence_seconds ?? config.silenceSeconds,
            minChunkSeconds: request.chunk_min_seconds ?? config.minChunkSeconds,
            maxChunkSeconds: request.chunk_max_seconds ?? config.maxChunkSeconds,
            sampleRate: config.sampleRate
        )

        let capture = SystemAudioCapture { [weak self] samples, inputRate in
            self?.ingest(samples: samples, inputRate: inputRate)
        }
        self.capture = capture

        do {
            try capture.start()
        } catch {
            self.capture = nil
            state = .idle
            sessionId = nil
            let message = permissionMessage(from: error)
            lastError = message
            throw AgentHTTPError(status: 503, detail: message)
        }

        state = .recording
        chunkStartOffset = TimelineClock(lessonStartedAt: startedAt).offsetSeconds()
    }

    func pause(sessionId expected: String) throws {
        lock.lock()
        defer { lock.unlock() }
        try requireSession(expected)
        guard state == .recording else {
            throw AgentHTTPError(status: 409, detail: "Session is not recording")
        }
        capture?.stop()
        finalizeLocked(reason: .pause)
        state = .paused
    }

    func resume(sessionId expected: String) throws {
        lock.lock()
        defer { lock.unlock() }
        try requireSession(expected)
        guard state == .paused else {
            throw AgentHTTPError(status: 409, detail: "Session is not paused")
        }
        guard let startedAt = lessonStartedAt else {
            throw AgentHTTPError(status: 500, detail: "Missing lesson start time")
        }

        sampleBuffer.removeAll(keepingCapacity: true)
        detector.reset()
        chunkStartOffset = TimelineClock(lessonStartedAt: startedAt).offsetSeconds()

        let capture = SystemAudioCapture { [weak self] samples, inputRate in
            self?.ingest(samples: samples, inputRate: inputRate)
        }
        self.capture = capture
        do {
            try capture.start()
        } catch {
            self.capture = nil
            let message = permissionMessage(from: error)
            lastError = message
            throw AgentHTTPError(status: 503, detail: message)
        }
        state = .recording
    }

    func stop(sessionId expected: String) throws {
        lock.lock()
        defer { lock.unlock() }
        try requireSession(expected)
        guard state == .recording || state == .paused else {
            throw AgentHTTPError(status: 409, detail: "Session cannot be stopped")
        }
        state = .stopping
        capture?.stop()
        capture = nil
        if state == .stopping {
            finalizeLocked(reason: .stop)
        }
        state = .idle
        sessionId = nil
        uploadURL = nil
    }

    func cancel(sessionId expected: String) throws {
        lock.lock()
        defer { lock.unlock() }
        try requireSession(expected)
        capture?.stop()
        capture = nil
        sampleBuffer.removeAll()
        detector.reset()
        state = .cancelled
        sessionId = nil
        uploadURL = nil
    }

    private func requireSession(_ expected: String) throws {
        guard let sessionId, sessionId == expected else {
            throw AgentHTTPError(status: 404, detail: "Unknown session")
        }
    }

    private func ingest(samples: [Float], inputRate: Double) {
        lock.lock()
        defer { lock.unlock() }
        guard state == .recording else { return }

        let mono = AudioResampler.resample(
            samples: samples,
            fromRate: inputRate,
            toRate: Double(config.sampleRate)
        )
        sampleBuffer.append(contentsOf: mono)
        let decision = detector.ingest(samples: mono)
        switch decision {
        case .continueChunk:
            break
        case .closeForSilence, .closeForMaxDuration:
            finalizeLocked(reason: .auto)
        }
    }

    private enum CloseReason {
        case auto
        case pause
        case stop
    }

    private func finalizeLocked(reason: CloseReason) {
        guard let startedAt = lessonStartedAt,
              let uploadURL,
              sessionId != nil else {
            sampleBuffer.removeAll()
            detector.reset()
            return
        }

        let samples = sampleBuffer
        sampleBuffer.removeAll(keepingCapacity: true)
        detector.reset()

        if samples.isEmpty || SilenceDetector.isEffectivelySilent(
            samples: samples,
            threshold: config.silenceRmsThreshold
        ) {
            // Never upload empty/silence-only chunks.
            if reason == .auto {
                chunkStartOffset = TimelineClock(lessonStartedAt: startedAt)
                    .offsetSeconds()
            }
            return
        }

        let endOffset = TimelineClock(lessonStartedAt: startedAt).offsetSeconds()
        let startOffset = chunkStartOffset
        let duration = max(0, endOffset - startOffset)
        guard duration > 0.2 else { return }

        let index = nextChunkIndex
        nextChunkIndex += 1
        chunkStartOffset = endOffset

        let wav = WAVWriter.writeMonoPCM16(samples: samples, sampleRate: config.sampleRate)
        let manifest = ChunkManifest(
            chunkIndex: index,
            startOffsetSeconds: startOffset,
            endOffsetSeconds: endOffset
        )

        let token = authToken
        let timeout = config.uploadTimeoutSeconds
        Task {
            do {
                try await ChunkUploader.upload(
                    wav: wav,
                    manifest: manifest,
                    uploadURL: uploadURL,
                    token: token,
                    timeout: timeout
                )
            } catch {
                // Keep capture alive; surface error via status.
                self.lock.lock()
                self.lastError = "Chunk upload failed: \(error.localizedDescription)"
                self.lock.unlock()
            }
        }
    }

    private func permissionMessage(from error: Error) -> String {
        let text = String(describing: error)
        if text.localizedCaseInsensitiveContains("permission")
            || text.localizedCaseInsensitiveContains("denied")
            || text.localizedCaseInsensitiveContains("tcc") {
            return """
            Screen Recording / System Audio permission is required. \
            Open System Settings → Privacy & Security → Screen & System Audio Recording, \
            enable access for mac-capture-agent, then restart the agent. Details: \(text)
            """
        }
        return "Failed to start system audio capture: \(text)"
    }
}

enum ChunkUploader {
    static func upload(
        wav: Data,
        manifest: ChunkManifest,
        uploadURL: URL,
        token: String,
        timeout: Double
    ) async throws {
        let boundary = "Boundary-\(UUID().uuidString)"
        var body = Data()

        func appendField(name: String, value: String) {
            body.append("--\(boundary)\r\n".data(using: .utf8)!)
            body.append(
                "Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n"
                    .data(using: .utf8)!
            )
            body.append("\(value)\r\n".data(using: .utf8)!)
        }

        appendField(name: "chunk_index", value: String(manifest.chunkIndex))
        appendField(
            name: "start_offset_seconds",
            value: String(manifest.startOffsetSeconds)
        )
        appendField(
            name: "end_offset_seconds",
            value: String(manifest.endOffsetSeconds)
        )
        appendField(
            name: "duration_seconds",
            value: String(manifest.durationSeconds)
        )

        let filename = String(format: "chunk_%04d.wav", manifest.chunkIndex)
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append(
            "Content-Disposition: form-data; name=\"audio\"; filename=\"\(filename)\"\r\n"
                .data(using: .utf8)!
        )
        body.append("Content-Type: audio/wav\r\n\r\n".data(using: .utf8)!)
        body.append(wav)
        body.append("\r\n".data(using: .utf8)!)
        body.append("--\(boundary)--\r\n".data(using: .utf8)!)

        var request = URLRequest(url: uploadURL)
        request.httpMethod = "POST"
        request.setValue(
            "multipart/form-data; boundary=\(boundary)",
            forHTTPHeaderField: "Content-Type"
        )
        if !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        request.timeoutInterval = timeout
        request.httpBody = body

        let (_, response) = try await URLSession.shared.data(for: request)
        let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(statusCode) else {
            throw AgentHTTPError(status: statusCode, detail: "Upload HTTP \(statusCode)")
        }
    }
}

struct AgentHTTPError: Error, LocalizedError, Sendable {
    var status: Int
    var detail: String
    var errorDescription: String? { detail }
}
