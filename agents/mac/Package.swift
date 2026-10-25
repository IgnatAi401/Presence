// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "PresenceAgent",
    platforms: [.macOS(.v14)],
    targets: [
        // 与系统无关的逻辑（项目名匹配、信号、离线队列），便于单元测试
        .target(name: "PresenceAgentCore"),
        // 菜单栏 App：采集前台信息并上报
        .executableTarget(name: "PresenceAgent", dependencies: ["PresenceAgentCore"]),
        .testTarget(name: "PresenceAgentCoreTests", dependencies: ["PresenceAgentCore"]),
    ],
    swiftLanguageModes: [.v5]
)
