import AVFoundation
import CoreMedia
import Foundation
@preconcurrency import ScreenCaptureKit

enum SystemAudioCaptureError: Error, Sendable {
    case noDisplay
    case streamFailed(String)
}

/// System-audio-only capture via ScreenCaptureKit.
///
/// `@unchecked Sendable` because:
/// - `stream` is mutated only from `start`/`stop` (serialized by `SessionController`'s lock)
/// - sample callbacks run on `outputQueue` and only invoke the `@Sendable` `onSamples` sink
/// - ScreenCaptureKit ObjC types are imported `@preconcurrency` (not fully Sendable-annotated)
final class SystemAudioCapture: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    private let onSamples: @Sendable ([Float], Double) -> Void
    private var stream: SCStream?
    private let outputQueue = DispatchQueue(label: "mac.capture.agent.audio")

    init(onSamples: @escaping @Sendable ([Float], Double) -> Void) {
        self.onSamples = onSamples
    }

    func start() throws {
        try AsyncBridge.runThrowing(timeoutSeconds: 20) { [self] in
            try await self.startAsync()
        }
    }

    func stop() {
        do {
            try AsyncBridge.runThrowing(timeoutSeconds: 10) { [self] in
                await self.stopAsync()
            }
        } catch {
            NSLog("SystemAudioCapture stop failed: \(error.localizedDescription)")
            stream = nil
        }
    }

    private func startAsync() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(
            false,
            onScreenWindowsOnly: true
        )
        guard let display = content.displays.first else {
            throw SystemAudioCaptureError.noDisplay
        }

        let filter = SCContentFilter(display: display, excludingWindows: [])
        let configuration = SCStreamConfiguration()
        configuration.capturesAudio = true
        configuration.excludesCurrentProcessAudio = true
        // Minimize video overhead; we only consume audio frames.
        configuration.width = 2
        configuration.height = 2
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        configuration.sampleRate = 48000
        configuration.channelCount = 1

        if #available(macOS 15.0, *) {
            configuration.captureMicrophone = false
        }

        let stream = SCStream(filter: filter, configuration: configuration, delegate: self)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: outputQueue)
        // Intentionally do not add .screen output.

        try await stream.startCapture()
        self.stream = stream
    }

    private func stopAsync() async {
        guard let stream else { return }
        do {
            try await stream.stopCapture()
        } catch {
            NSLog("SystemAudioCapture stopCapture error: \(error.localizedDescription)")
        }
        self.stream = nil
    }

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of type: SCStreamOutputType
    ) {
        guard type == .audio else { return }
        guard let samples = Self.floatSamples(from: sampleBuffer) else { return }
        let rate = sampleBuffer.formatDescription.map { format -> Double in
            let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(format)?.pointee
            return Double(asbd?.mSampleRate ?? 48000)
        } ?? 48000
        onSamples(samples, rate)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        // Surface via next start/status; keep process alive.
        NSLog("SystemAudioCapture stopped with error: \(error.localizedDescription)")
    }

    private static func floatSamples(from sampleBuffer: CMSampleBuffer) -> [Float]? {
        guard let formatDescription = sampleBuffer.formatDescription else { return nil }
        guard let asbdPointer = CMAudioFormatDescriptionGetStreamBasicDescription(
            formatDescription
        ) else {
            return nil
        }
        let asbd = asbdPointer.pointee
        let channels = Int(max(1, asbd.mChannelsPerFrame))

        guard let blockBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) else { return nil }
        var length = 0
        var dataPointer: UnsafeMutablePointer<Int8>?
        let status = CMBlockBufferGetDataPointer(
            blockBuffer,
            atOffset: 0,
            lengthAtOffsetOut: nil,
            totalLengthOut: &length,
            dataPointerOut: &dataPointer
        )
        guard status == kCMBlockBufferNoErr, let dataPointer, length > 0 else { return nil }

        if asbd.mFormatFlags & kAudioFormatFlagIsFloat != 0 {
            let floatCount = length / MemoryLayout<Float>.size
            let buffer = UnsafeRawPointer(dataPointer).bindMemory(
                to: Float.self,
                capacity: floatCount
            )
            let all = Array(UnsafeBufferPointer(start: buffer, count: floatCount))
            return downmix(all, channels: channels)
        }

        // Prefer 16-bit PCM fallback.
        let intCount = length / MemoryLayout<Int16>.size
        let buffer = UnsafeRawPointer(dataPointer).bindMemory(
            to: Int16.self,
            capacity: intCount
        )
        let ints = Array(UnsafeBufferPointer(start: buffer, count: intCount))
        let floats = ints.map { Float($0) / Float(Int16.max) }
        return downmix(floats, channels: channels)
    }

    private static func downmix(_ samples: [Float], channels: Int) -> [Float] {
        guard channels > 1 else { return samples }
        var mono: [Float] = []
        mono.reserveCapacity(samples.count / channels)
        var index = 0
        while index + channels <= samples.count {
            var sum: Float = 0
            for channel in 0..<channels {
                sum += samples[index + channel]
            }
            mono.append(sum / Float(channels))
            index += channels
        }
        return mono
    }
}
