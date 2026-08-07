import Foundation
import Testing
@testable import CaptureAgentCore

@Test
func closesAfterSilenceOnceMinimumReached() {
    var detector = SilenceDetector(
        rmsThreshold: 0.01,
        silenceSeconds: 0.1,
        minChunkSeconds: 0.2,
        maxChunkSeconds: 10,
        sampleRate: 10
    )

    // 3 samples of tone (~0.3s) then silence.
    let tone = [Float](repeating: 0.2, count: 3)
    #expect(detector.ingest(samples: tone) == .continueChunk)

    let silence = [Float](repeating: 0.0, count: 2)
    let decision = detector.ingest(samples: silence)
    #expect(decision == .closeForSilence)
}

@Test
func maxDurationClosesChunk() {
    var detector = SilenceDetector(
        rmsThreshold: 0.01,
        silenceSeconds: 5,
        minChunkSeconds: 20,
        maxChunkSeconds: 0.3,
        sampleRate: 10
    )
    let tone = [Float](repeating: 0.2, count: 4)
    #expect(detector.ingest(samples: tone) == .closeForMaxDuration)
}

@Test
func doesNotCloseOnSilenceBeforeMinimum() {
    var detector = SilenceDetector(
        rmsThreshold: 0.01,
        silenceSeconds: 0.1,
        minChunkSeconds: 1.0,
        maxChunkSeconds: 10,
        sampleRate: 10
    )
    let silence = [Float](repeating: 0.0, count: 5)
    #expect(detector.ingest(samples: silence) == .continueChunk)
}

@Test
func pauseGapsRemainVisibleInOffsets() {
    let start = Date(timeIntervalSince1970: 0)
    let clock = TimelineClock(lessonStartedAt: start)

    let chunk1End = clock.offsetSeconds(at: Date(timeIntervalSince1970: 420))
    #expect(abs(chunk1End - 420) < 0.001)

    // Pause until t=900, then resume.
    let chunk2Start = clock.offsetSeconds(at: Date(timeIntervalSince1970: 900))
    #expect(abs(chunk2Start - 900) < 0.001)
    #expect(chunk2Start - chunk1End > 60)
}

@Test
func writesRIFFHeader() {
    let data = WAVWriter.writeMonoPCM16(
        samples: [0.0, 0.5, -0.5],
        sampleRate: 16_000
    )
    #expect(String(data: data.prefix(4), encoding: .ascii) == "RIFF")
    #expect(String(data: data.subdata(in: 8..<12), encoding: .ascii) == "WAVE")
}
