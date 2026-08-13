import Foundation
import Darwin
import CaptureAgentCore

private final class AgentLifecycle: @unchecked Sendable {
    private let server: AgentHTTPServer
    private let controller: SessionController
    private let terminationSource: DispatchSourceSignal

    init(server: AgentHTTPServer, controller: SessionController) {
        self.server = server
        self.controller = controller

        signal(SIGTERM, SIG_IGN)
        terminationSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        terminationSource.setEventHandler { [weak self] in
            guard let self else { return }
            self.server.stop()
            self.controller.shutdown()
            exit(EXIT_SUCCESS)
        }
        terminationSource.resume()
    }
}

@main
struct MacCaptureAgentMain {
    static func main() throws {
        let config = AgentConfig.fromEnvironment()
        let controller = SessionController(config: config)
        let server = try AgentHTTPServer(config: config, controller: controller)
        server.start()

        NSLog(
            "mac-capture-agent ready on \(config.host):\(config.port) (system audio only, no microphone)"
        )

        let lifecycle = AgentLifecycle(server: server, controller: controller)
        withExtendedLifetime(lifecycle) {
            RunLoop.main.run()
        }
    }
}
