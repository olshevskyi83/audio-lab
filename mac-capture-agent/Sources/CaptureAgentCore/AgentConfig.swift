import Foundation

public struct AgentConfig: Sendable {
    public var host: String
    public var port: Int
    public var token: String
    public var silenceSeconds: Double
    public var minChunkSeconds: Double
    public var maxChunkSeconds: Double
    public var silenceRmsThreshold: Float
    public var sampleRate: Int
    public var uploadTimeoutSeconds: Double

    public init(
        host: String = "0.0.0.0",
        port: Int = 8011,
        token: String = "",
        silenceSeconds: Double = 5.0,
        minChunkSeconds: Double = 20.0,
        maxChunkSeconds: Double = 180.0,
        silenceRmsThreshold: Float = 0.01,
        sampleRate: Int = 16_000,
        uploadTimeoutSeconds: Double = 60.0
    ) {
        self.host = host
        self.port = port
        self.token = token
        self.silenceSeconds = silenceSeconds
        self.minChunkSeconds = minChunkSeconds
        self.maxChunkSeconds = maxChunkSeconds
        self.silenceRmsThreshold = silenceRmsThreshold
        self.sampleRate = sampleRate
        self.uploadTimeoutSeconds = uploadTimeoutSeconds
    }

    public static func fromEnvironment() -> AgentConfig {
        var config = AgentConfig()
        if let host = ProcessInfo.processInfo.environment["MAC_CAPTURE_HOST"], !host.isEmpty {
            config.host = host
        }
        if let port = ProcessInfo.processInfo.environment["MAC_CAPTURE_PORT"], let value = Int(port) {
            config.port = value
        }
        if let token = ProcessInfo.processInfo.environment["MAC_CAPTURE_AGENT_TOKEN"] {
            config.token = token
        }
        if let silence = ProcessInfo.processInfo.environment["LIVE_CHUNK_SILENCE_SECONDS"],
           let value = Double(silence) {
            config.silenceSeconds = value
        }
        if let minimum = ProcessInfo.processInfo.environment["LIVE_CHUNK_MIN_SECONDS"],
           let value = Double(minimum) {
            config.minChunkSeconds = value
        }
        if let maximum = ProcessInfo.processInfo.environment["LIVE_CHUNK_MAX_SECONDS"],
           let value = Double(maximum) {
            config.maxChunkSeconds = value
        }
        if let threshold = ProcessInfo.processInfo.environment["LIVE_SILENCE_RMS_THRESHOLD"],
           let value = Float(threshold) {
            config.silenceRmsThreshold = value
        }
        return config
    }
}
