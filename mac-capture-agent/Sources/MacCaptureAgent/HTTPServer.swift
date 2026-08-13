import Foundation
@preconcurrency import Network
import CaptureAgentCore

/// HTTP front-end for the capture agent.
///
/// Marked `@unchecked Sendable` because all request handling and Network.framework
/// callbacks are funneled onto `queue`. Immutable after `init` except for the
/// listener lifecycle, which is also started/observed only on `queue`.
/// `SessionController` is independently lock-serialized.
final class AgentHTTPServer: @unchecked Sendable {
    private let listener: NWListener
    private let controller: SessionController
    private let token: String
    private let queue = DispatchQueue(label: "mac.capture.agent.http")

    init(config: AgentConfig, controller: SessionController) throws {
        self.controller = controller
        self.token = config.token
        let parameters = NWParameters.tcp
        listener = try NWListener(
            using: parameters,
            on: NWEndpoint.Port(rawValue: UInt16(config.port))!
        )
    }

    func start() {
        listener.newConnectionHandler = { [weak self] connection in
            guard let self else { return }
            self.queue.async {
                self.handle(connection)
            }
        }
        listener.stateUpdateHandler = { state in
            if case .failed(let error) = state {
                NSLog("HTTP listener failed: \(error)")
            }
        }
        listener.start(queue: queue)
        NSLog("Mac Capture Agent listening")
    }

    func stop() {
        listener.cancel()
    }

    private func handle(_ connection: NWConnection) {
        connection.start(queue: queue)
        receive(on: connection, buffer: Data())
    }

    private func receive(on connection: NWConnection, buffer: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) {
            [weak self] data, _, isComplete, error in
            guard let self else { return }
            self.queue.async {
                self.handleReceive(
                    connection: connection,
                    buffer: buffer,
                    data: data,
                    isComplete: isComplete,
                    error: error
                )
            }
        }
    }

    private func handleReceive(
        connection: NWConnection,
        buffer: Data,
        data: Data?,
        isComplete: Bool,
        error: Error?
    ) {
        if let error {
            NSLog("Connection error: \(error)")
            connection.cancel()
            return
        }

        var next = buffer
        if let data {
            next.append(data)
        }

        if let headerEnd = next.range(of: Data("\r\n\r\n".utf8)) {
            let headerData = next.subdata(in: next.startIndex..<headerEnd.lowerBound)
            let headerText = String(data: headerData, encoding: .utf8) ?? ""
            let lines = headerText.components(separatedBy: "\r\n")
            guard let requestLine = lines.first else {
                respond(connection, status: 400, body: #"{"detail":"Bad request"}"#)
                return
            }
            let parts = requestLine.split(separator: " ")
            guard parts.count >= 2 else {
                respond(connection, status: 400, body: #"{"detail":"Bad request"}"#)
                return
            }
            let method = String(parts[0])
            let path = String(parts[1])

            var headers: [String: String] = [:]
            for line in lines.dropFirst() {
                if let separator = line.firstIndex(of: ":") {
                    let key = String(line[..<separator])
                        .trimmingCharacters(in: .whitespaces)
                        .lowercased()
                    let value = String(line[line.index(after: separator)...])
                        .trimmingCharacters(in: .whitespaces)
                    headers[key] = value
                }
            }

            let contentLength = Int(headers["content-length"] ?? "0") ?? 0
            let bodyStart = headerEnd.upperBound
            let availableBody = next.count - bodyStart
            if availableBody < contentLength {
                receive(on: connection, buffer: next)
                return
            }

            let body = contentLength > 0
                ? next.subdata(in: bodyStart..<(bodyStart + contentLength))
                : Data()

            dispatch(
                method: method,
                path: path,
                headers: headers,
                body: body,
                connection: connection
            )
            return
        }

        if isComplete {
            connection.cancel()
        } else {
            receive(on: connection, buffer: next)
        }
    }

    private func dispatch(
        method: String,
        path: String,
        headers: [String: String],
        body: Data,
        connection: NWConnection
    ) {
        if path != "/health" {
            if !authorize(headers: headers) {
                respond(connection, status: 401, body: #"{"detail":"Unauthorized"}"#)
                return
            }
        }

        do {
            switch (method, path) {
            case ("GET", "/health"):
                respond(
                    connection,
                    status: 200,
                    body: #"{"status":"ok","service":"mac-capture-agent"}"#
                )

            case ("GET", "/status"):
                let payload = controller.statusPayload()
                respond(connection, status: 200, json: payload)

            case ("POST", "/sessions/start"):
                let request = try JSONDecoder().decode(StartSessionRequest.self, from: body)
                try controller.start(request: request)
                respond(connection, status: 200, json: controller.statusPayload())

            case ("POST", let pausePath)
                where pausePath.hasPrefix("/sessions/") && pausePath.hasSuffix("/pause"):
                let sessionId = extractSessionId(from: pausePath)
                try controller.pause(sessionId: sessionId)
                respond(connection, status: 200, json: controller.statusPayload())

            case ("POST", let resumePath)
                where resumePath.hasPrefix("/sessions/") && resumePath.hasSuffix("/resume"):
                let sessionId = extractSessionId(from: resumePath)
                try controller.resume(sessionId: sessionId)
                respond(connection, status: 200, json: controller.statusPayload())

            case ("POST", let stopPath)
                where stopPath.hasPrefix("/sessions/") && stopPath.hasSuffix("/stop"):
                let sessionId = extractSessionId(from: stopPath)
                try controller.stop(sessionId: sessionId)
                respond(connection, status: 200, json: controller.statusPayload())

            case ("POST", let cancelPath)
                where cancelPath.hasPrefix("/sessions/") && cancelPath.hasSuffix("/cancel"):
                let sessionId = extractSessionId(from: cancelPath)
                try controller.cancel(sessionId: sessionId)
                respond(connection, status: 200, json: ["state": "cancelled"])

            default:
                respond(connection, status: 404, body: #"{"detail":"Not found"}"#)
            }
        } catch let error as AgentHTTPError {
            let escaped = error.detail.replacingOccurrences(of: "\"", with: "\\\"")
            respond(connection, status: error.status, body: "{\"detail\":\"\(escaped)\"}")
        } catch {
            let escaped = error.localizedDescription
                .replacingOccurrences(of: "\"", with: "\\\"")
            respond(connection, status: 500, body: "{\"detail\":\"\(escaped)\"}")
        }
    }

    private func authorize(headers: [String: String]) -> Bool {
        if token.isEmpty {
            return true
        }
        if let bearer = headers["authorization"],
           bearer.lowercased().hasPrefix("bearer ") {
            let value = String(bearer.dropFirst(7))
                .trimmingCharacters(in: .whitespaces)
            if value == token {
                return true
            }
        }
        if let headerToken = headers["x-live-token"], headerToken == token {
            return true
        }
        return false
    }

    private func extractSessionId(from path: String) -> String {
        // /sessions/{id}/{action}
        let parts = path.split(separator: "/").map(String.init)
        if parts.count >= 3 {
            return parts[1]
        }
        return ""
    }

    private func respond(_ connection: NWConnection, status: Int, body: String) {
        let reason: String
        switch status {
        case 200: reason = "OK"
        case 400: reason = "Bad Request"
        case 401: reason = "Unauthorized"
        case 404: reason = "Not Found"
        case 409: reason = "Conflict"
        case 503: reason = "Service Unavailable"
        default: reason = "Error"
        }
        let data = body.data(using: .utf8) ?? Data()
        var response = "HTTP/1.1 \(status) \(reason)\r\n"
        response += "Content-Type: application/json\r\n"
        response += "Content-Length: \(data.count)\r\n"
        response += "Connection: close\r\n\r\n"
        var payload = response.data(using: .utf8) ?? Data()
        payload.append(data)
        connection.send(content: payload, completion: .contentProcessed { _ in
            connection.cancel()
        })
    }

    private func respond(_ connection: NWConnection, status: Int, json: [String: Any]) {
        let data = (try? JSONSerialization.data(withJSONObject: json, options: []))
            ?? Data("{}".utf8)
        let body = String(data: data, encoding: .utf8) ?? "{}"
        respond(connection, status: status, body: body)
    }
}
