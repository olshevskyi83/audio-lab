// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MacCaptureAgent",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .library(
            name: "CaptureAgentCore",
            targets: ["CaptureAgentCore"]
        ),
        .executable(
            name: "mac-capture-agent",
            targets: ["MacCaptureAgent"]
        ),
    ],
    targets: [
        .target(
            name: "CaptureAgentCore",
            path: "Sources/CaptureAgentCore"
        ),
        .executableTarget(
            name: "MacCaptureAgent",
            dependencies: ["CaptureAgentCore"],
            path: "Sources/MacCaptureAgent"
        ),
        .testTarget(
            name: "CaptureAgentCoreTests",
            dependencies: ["CaptureAgentCore"],
            path: "Tests/CaptureAgentCoreTests"
        ),
    ]
)
