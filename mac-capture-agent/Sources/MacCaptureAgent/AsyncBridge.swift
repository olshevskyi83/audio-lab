import Foundation

/// Thread-safe one-shot result carrier for bridging async work into sync callers.
/// Safe as `@unchecked Sendable` because all access is guarded by `lock`.
final class OnceResultBox<T>: @unchecked Sendable {
    private let lock = NSLock()
    private var result: Result<T, Error>?

    func set(_ result: Result<T, Error>) {
        lock.lock()
        defer { lock.unlock() }
        precondition(self.result == nil, "OnceResultBox set more than once")
        self.result = result
    }

    func get() throws -> T {
        lock.lock()
        defer { lock.unlock() }
        guard let result else {
            throw AgentHTTPError(status: 500, detail: "Missing async bridge result")
        }
        switch result {
        case .success(let value):
            return value
        case .failure(let error):
            throw error
        }
    }
}

enum AsyncBridge {
    /// Run an async throwing operation to completion on a cooperative task.
    /// Uses a locked result box instead of mutating locals from a `@Sendable` closure.
    static func runThrowing<T: Sendable>(
        timeoutSeconds: TimeInterval = 30,
        operation: @escaping @Sendable () async throws -> T
    ) throws -> T {
        let box = OnceResultBox<T>()
        let semaphore = DispatchSemaphore(value: 0)

        Task {
            do {
                let value = try await operation()
                box.set(.success(value))
            } catch {
                box.set(.failure(error))
            }
            semaphore.signal()
        }

        let waited = semaphore.wait(timeout: .now() + timeoutSeconds)
        if waited == .timedOut {
            throw AgentHTTPError(
                status: 503,
                detail: "Timed out waiting for async capture operation"
            )
        }
        return try box.get()
    }

    static func runThrowing(
        timeoutSeconds: TimeInterval = 30,
        operation: @escaping @Sendable () async throws -> Void
    ) throws {
        let _: Bool = try runThrowing(timeoutSeconds: timeoutSeconds) {
            try await operation()
            return true
        }
    }
}
