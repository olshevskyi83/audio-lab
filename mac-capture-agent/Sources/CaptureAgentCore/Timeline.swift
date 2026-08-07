import Foundation

public struct TimelineClock: Sendable {
    public let lessonStartedAt: Date

    public init(lessonStartedAt: Date) {
        self.lessonStartedAt = lessonStartedAt
    }

    public func offsetSeconds(at date: Date = Date()) -> Double {
        max(0, date.timeIntervalSince(lessonStartedAt))
    }
}

public struct ChunkManifest: Equatable, Sendable {
    public var chunkIndex: Int
    public var startOffsetSeconds: Double
    public var endOffsetSeconds: Double
    public var durationSeconds: Double

    public init(
        chunkIndex: Int,
        startOffsetSeconds: Double,
        endOffsetSeconds: Double
    ) {
        self.chunkIndex = chunkIndex
        self.startOffsetSeconds = startOffsetSeconds
        self.endOffsetSeconds = endOffsetSeconds
        self.durationSeconds = max(0, endOffsetSeconds - startOffsetSeconds)
    }
}
