import Foundation
import CaptureAgentCore

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

        RunLoop.main.run()
    }
}
