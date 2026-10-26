import Foundation

/// 上报接口里 data 字段的取值（只用到字符串、数字和布尔值）。
public enum JSONValue: Codable, Equatable, Sendable {
    case string(String)
    case number(Double)
    case bool(Bool)

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let b = try? c.decode(Bool.self) {
            self = .bool(b)
        } else if let n = try? c.decode(Double.self) {
            self = .number(n)
        } else {
            self = .string(try c.decode(String.self))
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case let .string(s): try c.encode(s)
        case let .number(n): try c.encode(n)
        case let .bool(b): try c.encode(b)
        }
    }
}

/// 一条上报信号，对应服务器 POST /api/signal 的 signals[] 一项。
public struct Signal: Codable, Equatable, Sendable {
    public var kind: String
    public var ts: Double
    public var ttl: Double
    public var data: [String: JSONValue]

    public init(kind: String, ts: Double, ttl: Double, data: [String: JSONValue] = [:]) {
        self.kind = kind
        self.ts = ts
        self.ttl = ttl
        self.data = data
    }
}

public struct SignalBatch: Codable, Sendable {
    public var signals: [Signal]

    public init(signals: [Signal]) {
        self.signals = signals
    }
}

/// Mac 此刻的前台状态。
public struct DesktopState: Equatable, Sendable {
    public var bundleId: String
    public var app: String
    public var project: String?
    /// 设置里给这个项目选的类别（research / dev），服务器优先按它分类
    public var category: String?
    public var domain: String?
    /// Safari 当前网页的标题（最多 200 字）
    public var title: String?
    /// Safari 当前网站仅私有记录：仍上报域名和标题，公开页隐藏详情
    public var hidden: Bool
    public var idleSec: Double
    public var locked: Bool

    public init(bundleId: String, app: String, project: String? = nil, category: String? = nil, domain: String? = nil,
                title: String? = nil, hidden: Bool = false, idleSec: Double = 0, locked: Bool = false) {
        self.bundleId = bundleId
        self.app = app
        self.project = project
        self.category = category
        self.domain = domain
        self.title = title.map { String($0.prefix(200)) }
        self.hidden = hidden
        self.idleSec = idleSec
        self.locked = locked
    }

    /// 用来判断“有没有变化”：空闲秒数本身不算，只看是否越过离开阈值。
    public func changeKey(awaySec: Double) -> String {
        [bundleId, project ?? "", category ?? "", domain ?? "", title ?? "", hidden ? "hidden" : "", locked ? "locked" : "",
         idleSec >= awaySec ? "away" : ""]
            .joined(separator: "|")
    }

    public func signal(ts: Double, ttl: Double) -> Signal {
        var data: [String: JSONValue] = [
            "bundle_id": .string(bundleId),
            "app": .string(app),
            "idle_sec": .number(idleSec.rounded()),
        ]
        if let project {
            data["project"] = .string(project)
            if let category { data["category"] = .string(category) }
        }
        if let domain { data["domain"] = .string(domain) }
        if let title, !title.isEmpty { data["title"] = .string(String(title.prefix(200))) }
        if hidden {
            data["private"] = .bool(true)
            data["hidden"] = .bool(true) // 兼容旧服务器，避免升级期间公开详情
        }
        if locked { data["locked"] = .bool(true) }
        return Signal(kind: "desktop", ts: ts, ttl: ttl, data: data)
    }
}

/// 决定什么时候上报：状态变化时立即发；没变化时按心跳间隔发。
public struct ReportPolicy: Sendable {
    public private(set) var lastKey: String?
    public private(set) var lastSent: Double?

    public init() {}

    public mutating func shouldSend(_ state: DesktopState, now: Double, heartbeatSec: Double, awaySec: Double) -> Bool {
        let key = state.changeKey(awaySec: awaySec)
        if key != lastKey || lastSent.map({ now - $0 >= heartbeatSec }) ?? true {
            lastKey = key
            lastSent = now
            return true
        }
        return false
    }

    /// 暂停或配置变化后，下一次采样必定上报
    public mutating func reset() {
        lastKey = nil
        lastSent = nil
    }
}
