import Foundation
import Darwin

public struct SilenceDetector: Sendable {
    public var rmsThreshold: Float
    public var silenceSeconds: Double
    public var minChunkSeconds: Double
    public var maxChunkSeconds: Double
    public var sampleRate: Int

    private var consecutiveSilentSamples: Int = 0
    private var chunkSamples: Int = 0

    public init(
        rmsThreshold: Float = 0.01,
        silenceSeconds: Double = 5.0,
        minChunkSeconds: Double = 20.0,
        maxChunkSeconds: Double = 180.0,
        sampleRate: Int = 16_000
    ) {
        self.rmsThreshold = rmsThreshold
        self.silenceSeconds = silenceSeconds
        self.minChunkSeconds = minChunkSeconds
        self.maxChunkSeconds = maxChunkSeconds
        self.sampleRate = sampleRate
    }

    public mutating func reset() {
        consecutiveSilentSamples = 0
        chunkSamples = 0
    }

    public var currentChunkSeconds: Double {
        Double(chunkSamples) / Double(sampleRate)
    }

    public enum Decision: Equatable {
        case continueChunk
        case closeForSilence
        case closeForMaxDuration
    }

    public mutating func ingest(samples: [Float]) -> Decision {
        guard !samples.isEmpty else { return .continueChunk }

        chunkSamples += samples.count

        let energy = Self.rms(samples)
        let silent = energy < rmsThreshold
        if silent {
            consecutiveSilentSamples += samples.count
        } else {
            consecutiveSilentSamples = 0
        }

        let chunkSeconds = Double(chunkSamples) / Double(sampleRate)
        if chunkSeconds >= maxChunkSeconds {
            return .closeForMaxDuration
        }

        let silentSeconds = Double(consecutiveSilentSamples) / Double(sampleRate)
        if silentSeconds >= silenceSeconds && chunkSeconds >= minChunkSeconds {
            return .closeForSilence
        }

        return .continueChunk
    }

    public static func rms(_ samples: [Float]) -> Float {
        guard !samples.isEmpty else { return 0 }
        var sum: Float = 0
        for sample in samples {
            sum += sample * sample
        }
        return sqrt(sum / Float(samples.count))
    }

    public static func isEffectivelySilent(
        samples: [Float],
        threshold: Float = 0.01
    ) -> Bool {
        rms(samples) < threshold
    }
}
