import Foundation

/// 待发送的信号队列，保存在磁盘上：服务器没开或断网时先存着，恢复后按时间顺序补发。
public struct SignalQueue: Sendable {
    public private(set) var items: [Signal] = []
    public let fileURL: URL?
    public let limit: Int

    public init(fileURL: URL?, limit: Int = 5000) {
        self.fileURL = fileURL
        self.limit = limit
        if let fileURL, let data = try? Data(contentsOf: fileURL),
           let saved = try? JSONDecoder().decode([Signal].self, from: data) {
            items = saved
        }
    }

    public var count: Int { items.count }
    public var isEmpty: Bool { items.isEmpty }

    public mutating func append(_ signal: Signal) {
        items.append(signal)
        if items.count > limit {
            items.removeFirst(items.count - limit) // 太久没连上时丢掉最旧的
        }
        save()
    }

    /// 取出最前面的至多 max 条（服务器一次最多收 500 条）
    public func peek(_ max: Int = 500) -> [Signal] {
        Array(items.prefix(max))
    }

    public mutating func removeFirst(_ n: Int) {
        items.removeFirst(min(n, items.count))
        save()
    }

    private func save() {
        guard let fileURL else { return }
        do {
            try FileManager.default.createDirectory(
                at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONEncoder().encode(items).write(to: fileURL, options: .atomic)
        } catch {
            NSLog("PresenceAgent: 保存离线队列失败 \(error)")
        }
    }
}
