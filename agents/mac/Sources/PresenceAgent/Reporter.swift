import Foundation
import PresenceAgentCore

/// 负责把队列里的信号发给服务器；失败时保留在磁盘队列里，按退避时间重试。
actor Reporter {
    enum Outcome: Equatable, Sendable {
        case idle
        case sent(Date)
        case failed(String)
        case unauthorized
        case notConfigured
    }

    private var queue: SignalQueue
    private var config: AgentConfig
    private var flushing = false
    private var retryDelay: Double = 5
    private var retryTask: Task<Void, Never>?
    private let session: URLSession
    private let onUpdate: @Sendable (Outcome, Int, Signal?) -> Void

    /// onUpdate 的第三个参数：刚送达服务器的最新一条信号（没有新送达时为 nil）
    init(config: AgentConfig, queueURL: URL, onUpdate: @escaping @Sendable (Outcome, Int, Signal?) -> Void) {
        self.config = config
        self.queue = SignalQueue(fileURL: queueURL)
        self.onUpdate = onUpdate
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 15
        cfg.waitsForConnectivity = false
        session = URLSession(configuration: cfg)
    }

    func update(config: AgentConfig) {
        self.config = config
        retryDelay = 5
        Task { await flush() }
    }

    func enqueue(_ signal: Signal) async {
        queue.append(signal)
        await flush()
    }

    var pending: Int { queue.count }

    func flush() async {
        guard !flushing else { return }
        flushing = true
        defer { flushing = false }

        guard let url = config.signalURL, !config.token.isEmpty else {
            onUpdate(.notConfigured, queue.count, nil)
            return
        }
        while !queue.isEmpty {
            let batch = queue.peek()
            var request = URLRequest(url: url)
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.setValue("Bearer \(config.token)", forHTTPHeaderField: "Authorization")
            do {
                request.httpBody = try JSONEncoder().encode(SignalBatch(signals: batch))
                let (_, response) = try await session.data(for: request)
                let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                switch status {
                case 200 ..< 300:
                    queue.removeFirst(batch.count)
                    retryDelay = 5
                    onUpdate(.sent(Date()), queue.count, batch.last)
                case 401:
                    // 令牌错误：保留队列，等改好令牌后再发
                    onUpdate(.unauthorized, queue.count, nil)
                    return
                case 400, 422:
                    // 服务器不认这批数据，重发也没用，丢掉避免卡住队列
                    queue.removeFirst(batch.count)
                    onUpdate(.failed("服务器拒收（\(status)）"), queue.count, nil)
                default:
                    scheduleRetry("服务器返回 \(status)")
                    return
                }
            } catch {
                scheduleRetry("连不上服务器")
                return
            }
        }
    }

    private func scheduleRetry(_ reason: String) {
        onUpdate(.failed(reason), queue.count, nil)
        retryTask?.cancel()
        let delay = retryDelay
        retryDelay = min(retryDelay * 2, 300)
        retryTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(delay))
            guard !Task.isCancelled else { return }
            await self?.flush()
        }
    }
}
