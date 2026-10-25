import Foundation

/// 小工具的本地配置，保存在 ~/Library/Application Support/PresenceAgent/config.json（权限 600，不进 Git）。
public struct AgentConfig: Codable, Equatable, Sendable {
    /// 接口前缀，例如 https://api.ignat.ai/presence/api
    public var server: String = "https://api.ignat.ai/presence/api"
    /// 设备令牌（服务器 settings.toml 里 [devices] 的 mac 那一行）
    public var token: String = ""
    /// 项目名只从这些目录下的文件夹名里取，窗口标题的其他内容不会上传
    public var projectRoots: [String] = ["~/Developer"]
    /// 会读取窗口标题来识别项目名的 App（bundle id）
    public var titleApps: [String] = [
        "com.microsoft.VSCode",
        "com.todesktop.230313mzl4w4u92", // Cursor
        "com.apple.dt.Xcode",
        "com.apple.Terminal",
        "com.googlecode.iterm2",
        "com.mitchellh.ghostty",
        "com.anthropic.claudefordesktop",
        "com.openai.codex",
    ]
    /// 没有变化时多久发一次心跳（秒）
    public var heartbeatSec: Double = 120
    /// 每条信号的有效期（秒）；超过没有新信号，服务器视为离线
    public var ttlSec: Double = 300
    /// 空闲超过多久立即上报一次，让服务器显示“离开”（秒）
    public var awaySec: Double = 600
    /// 检查窗口标题、Safari 网址和空闲时间的间隔（秒）
    public var pollSec: Double = 5
    /// 窗口标题里找不到项目名时，这些 App 改从 Claude Code / Codex 的会话记录里找工作区
    public var agentApps: [String: String] = [
        "com.anthropic.claudefordesktop": "claude",
        "com.openai.codex": "codex",
        "com.apple.Terminal": "any",
        "com.googlecode.iterm2": "any",
        "com.mitchellh.ghostty": "any",
    ]
    /// 会话记录多久之内有更新才算“正在用”（秒）
    public var agentActiveSec: Double = 1800
    /// 不公开具体内容的网站（子域名自动包含）：不上报域名、标题和视频，网页上只显示“网上冲浪”
    public var hiddenDomains: [String] = []

    public init() {}

    public init(from decoder: Decoder) throws {
        // 缺少的字段用默认值，方便以后加配置项
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let d = AgentConfig()
        server = try c.decodeIfPresent(String.self, forKey: .server) ?? d.server
        token = try c.decodeIfPresent(String.self, forKey: .token) ?? d.token
        projectRoots = try c.decodeIfPresent([String].self, forKey: .projectRoots) ?? d.projectRoots
        titleApps = try c.decodeIfPresent([String].self, forKey: .titleApps) ?? d.titleApps
        heartbeatSec = try c.decodeIfPresent(Double.self, forKey: .heartbeatSec) ?? d.heartbeatSec
        ttlSec = try c.decodeIfPresent(Double.self, forKey: .ttlSec) ?? d.ttlSec
        awaySec = try c.decodeIfPresent(Double.self, forKey: .awaySec) ?? d.awaySec
        pollSec = try c.decodeIfPresent(Double.self, forKey: .pollSec) ?? d.pollSec
        agentApps = try c.decodeIfPresent([String: String].self, forKey: .agentApps) ?? d.agentApps
        agentActiveSec = try c.decodeIfPresent(Double.self, forKey: .agentActiveSec) ?? d.agentActiveSec
        hiddenDomains = try c.decodeIfPresent([String].self, forKey: .hiddenDomains) ?? d.hiddenDomains
    }

    public var signalURL: URL? { endpoint("signal") }

    /// host 是否在黑名单里：完全相同，或是黑名单域名的子域名（www.zhihu.com 命中 zhihu.com）
    public func hides(host: String?) -> Bool {
        guard let host = host?.lowercased() else { return false }
        return hiddenDomains.contains { d in host == d || host.hasSuffix("." + d) }
    }

    /// 把用户输入的黑名单（逗号、空格或换行分隔，可以粘贴网址）整理成域名列表
    public static func parseDomains(_ text: String) -> [String] {
        var out: [String] = []
        for raw in text.split(whereSeparator: { ",，;； \n\t".contains($0) }) {
            var s = raw.lowercased()
            if let host = hostOf(s) { s = host }
            s = s.trimmingCharacters(in: CharacterSet(charactersIn: "./"))
            if s.hasPrefix("www.") { s.removeFirst(4) }
            if s.contains("."), !out.contains(s) { out.append(s) }
        }
        return out
    }

    /// 封面图片的上传 / 查询地址：media/{sha256}
    public func mediaURL(_ sha256: String) -> URL? { endpoint("media/" + sha256) }

    private func endpoint(_ path: String) -> URL? {
        let base = server.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !base.isEmpty else { return nil }
        return URL(string: base.hasSuffix("/") ? base + path : base + "/" + path)
    }
}

public enum ConfigStore {
    public static var directory: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("PresenceAgent", isDirectory: true)
    }

    public static func load(from url: URL = directory.appendingPathComponent("config.json")) -> AgentConfig {
        guard let data = try? Data(contentsOf: url),
              let config = try? JSONDecoder().decode(AgentConfig.self, from: data)
        else { return AgentConfig() }
        return config
    }

    public static func save(_ config: AgentConfig, to url: URL = directory.appendingPathComponent("config.json")) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        try encoder.encode(config).write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }
}
