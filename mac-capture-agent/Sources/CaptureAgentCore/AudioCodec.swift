import Foundation

public enum WAVWriter {
    public static func writeMonoPCM16(
        samples: [Float],
        sampleRate: Int
    ) -> Data {
        let clamped = samples.map { sample -> Int16 in
            let clipped = max(-1.0, min(1.0, sample))
            return Int16((clipped * Float(Int16.max)).rounded())
        }

        var data = Data()
        let dataSize = UInt32(clamped.count * 2)
        let riffSize = UInt32(36 + dataSize)

        func appendASCII(_ value: String) {
            data.append(contentsOf: value.utf8)
        }

        func appendUInt16(_ value: UInt16) {
            var le = value.littleEndian
            withUnsafeBytes(of: &le) { data.append(contentsOf: $0) }
        }

        func appendUInt32(_ value: UInt32) {
            var le = value.littleEndian
            withUnsafeBytes(of: &le) { data.append(contentsOf: $0) }
        }

        appendASCII("RIFF")
        appendUInt32(riffSize)
        appendASCII("WAVE")
        appendASCII("fmt ")
        appendUInt32(16) // PCM fmt chunk size
        appendUInt16(1) // audio format PCM
        appendUInt16(1) // mono
        appendUInt32(UInt32(sampleRate))
        appendUInt32(UInt32(sampleRate * 2)) // byte rate
        appendUInt16(2) // block align
        appendUInt16(16) // bits per sample
        appendASCII("data")
        appendUInt32(dataSize)

        for sample in clamped {
            var le = sample.littleEndian
            withUnsafeBytes(of: &le) { data.append(contentsOf: $0) }
        }

        return data
    }
}

public enum AudioResampler {
    /// Linear resample float mono samples to a target rate.
    public static func resample(
        samples: [Float],
        fromRate: Double,
        toRate: Double
    ) -> [Float] {
        guard fromRate > 0, toRate > 0, !samples.isEmpty else { return samples }
        if abs(fromRate - toRate) < 0.5 {
            return samples
        }

        let ratio = toRate / fromRate
        let outCount = max(1, Int((Double(samples.count) * ratio).rounded()))
        var output = [Float](repeating: 0, count: outCount)

        for index in 0..<outCount {
            let srcPos = Double(index) / ratio
            let left = Int(srcPos)
            let right = min(left + 1, samples.count - 1)
            let frac = Float(srcPos - Double(left))
            output[index] = samples[left] * (1 - frac) + samples[right] * frac
        }

        return output
    }
}
