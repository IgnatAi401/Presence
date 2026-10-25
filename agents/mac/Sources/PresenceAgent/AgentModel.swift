import AppKit
import Combine
import PresenceAgentCore

/// 采样 → 判断是否需要上报 → 交给 Reporter；同时给菜单栏提供显示用的状态。
@MainActor
final class AgentModel: ObservableObject {
    static let shared = AgentModel()

    @Published private(set) var config: AgentConfig
    @Published private(set) var current: DesktopState?
    @Published private(set) var outcome: Reporter.Outcome = .idle
    @Published private(set) var pendingCount = 0
    @Published private(set) var pausedUntil: Date?
    @Published private(set) var accessibilityOK = SystemProbe.accessibilityTrusted
    @Published private(set) var safariDenied = false
    @Published private(set) var musicDenied = false
    /// Safari 里有视频页，但没开“允许来自 Apple 事件的 JavaScript”，读不到播放状态
    @Published private(set) var videoBlocked = false
    /// “音乐”正在播放或暂停中的曲目（没在放时为 nil）
    @Published private(set) var music: MusicState?
    /// Safari 里正在看的 B站 / YouTube 视频（没在看时为 nil）
    @Published private(set) var video: VideoState?
    @Published private(set) var projectCount = 0
    /// 最新生成的一条信号，以及最新送达服务器的一条
    @Published private(set) var lastSignal: Signal?
    @Published private(set) var lastDelivered: Signal?

    private var policy = ReportPolicy()
    private var musicPolicy = MusicPolicy()
    private var videoPolicy = PlaybackPolicy<VideoState>()
    /// 当前曲目的封面：读一次就缓存；没读到时过一会儿再试（在线曲目的封面可能还没下载好）
    private var cover: (trackId: String, at: Date, data: Data?, sha256: String?)?
    private var uploadedCovers: Set<String> = []
    private var coverUploading: String?
    private var coverRetryAt = Date.distantPast
    private var matcher = ProjectMatcher(names: [])
    private var matcherScannedAt = Date.distantPast
    private var agentCache: (key: String, at: Date, project: String?)?
    private var claudeTitles: [String: String] = [:]
    private var claudeTitlesAt = Date.distantPast
    private var screenLocked = false
    private var asleep = false
    private var displayAsleep = false
    private var timer: Timer?
    private var observers: [NSObjectProtocol] = []
    private var started = false

    private lazy var reporter = Reporter(
        config: config,
        queueURL: ConfigStore.directory.appendingPathComponent("queue.json")
    ) { [weak self] outcome, pending, delivered in
        Task { @MainActor in
            self?.outcome = outcome
            self?.pendingCount = pending
            if let delivered { self?.lastDelivered = delivered }
        }
    }

    private init() {
        config = ConfigStore.load()
    }

    var paused: Bool { pausedUntil.map { $0 > Date() } ?? false }
    var locked: Bool { screenLocked || asleep || displayAsleep }
    var configured: Bool { config.signalURL != nil && !config.token.isEmpty }

    // MARK: 启动与系统通知

    func start() {
        guard !started else { return }
        started = true
        if !SystemProbe.accessibilityTrusted {
            SystemProbe.requestAccessibility()
        }

        let ws = NSWorkspace.shared.notificationCenter
        observe(ws, NSWorkspace.didActivateApplicationNotification) { $0.sample() }
        observe(ws, NSWorkspace.willSleepNotification) { $0.asleep = true; $0.sample() }
        observe(ws, NSWorkspace.didWakeNotification) { $0.asleep = false; $0.sample() }
        observe(ws, NSWorkspace.screensDidSleepNotification) { $0.displayAsleep = true; $0.sample() }
        observe(ws, NSWorkspace.screensDidWakeNotification) { $0.displayAsleep = false; $0.sample() }
        let dnc = DistributedNotificationCenter.default()
        observe(dnc, Notification.Name("com.apple.screenIsLocked")) { $0.screenLocked = true; $0.sample() }
        observe(dnc, Notification.Name("com.apple.screenIsUnlocked")) { $0.screenLocked = false; $0.sample() }
        // “音乐”换歌、播放 / 暂停时发出的通知，收到后立即读一次（不用等下一次定时检查）
        observe(dnc, Notification.Name("com.apple.Music.playerInfo")) { $0.sampleMusic() }

        restartTimer()
        _ = projectMatcher() // 先扫描一次，设置里的项目数量才准确
        sample()
        sampleMusic()
        sampleVideo()
        Task { await reporter.flush() }
    }

    private func observe(_ center: NotificationCenter, _ name: Notification.Name,
                         _ handler: @escaping @MainActor (AgentModel) -> Void) {
        observers.append(center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated {
                if let self { handler(self) }
            }
        })
    }

    private func restartTimer() {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: max(1, config.pollSec), repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.sample()
                self?.sampleMusic()
                self?.sampleVideo()
            }
        }
        timer?.tolerance = 1
    }

    // MARK: 采样

    func sample() {
        accessibilityOK = SystemProbe.accessibilityTrusted
        if let until = pausedUntil {
            if until > Date() { return }
            pausedUntil = nil
            policy.reset()
        }
        guard let front = SystemProbe.frontApp(), front.bundleId != Bundle.main.bundleIdentifier else { return }

        let now = Date().timeIntervalSince1970
        let idle = SystemProbe.idleSeconds()
        var state = DesktopState(bundleId: front.bundleId, app: front.name, idleSec: idle, locked: locked)

        // 锁屏或已离开时不读标题和网址，避免无谓的系统调用和权限弹窗
        if !state.locked && idle < config.awaySec {
            var title: String?
            if config.titleApps.contains(front.bundleId) {
                title = SystemProbe.focusedWindowTitle(pid: front.pid)
                state.project = title.flatMap { projectMatcher().match(title: $0) }
            }
            if state.project == nil, let tool = config.agentApps[front.bundleId] {
                state.project = agentProject(tool: tool, pid: front.pid, windowTitle: title)
            }
            if front.bundleId == SystemProbe.safariBundleId {
                switch SystemProbe.safariPage() {
                case let .page(url, title):
                    safariDenied = false
                    let host = hostOf(url)
                    if config.hides(host: host) {
                        state.hidden = true
                    } else {
                        state.domain = host
                        state.title = host == nil ? nil : title
                    }
                case .denied:
                    safariDenied = true
                }
            }
        } else if let prev = current, prev.bundleId == state.bundleId {
            // 离开期间沿用之前识别到的项目和网站
            state.project = prev.project
            state.domain = prev.domain
            state.title = prev.title
            state.hidden = prev.hidden
        }

        current = state
        if policy.shouldSend(state, now: now, heartbeatSec: config.heartbeatSec, awaySec: config.awaySec) {
            let signal = state.signal(ts: now, ttl: config.ttlSec)
            lastSignal = signal
            Task { await reporter.enqueue(signal) }
        }
    }

    // MARK: 音乐

    func sampleMusic() {
        guard !paused, !asleep else { return }
        var state: MusicState?
        switch SystemProbe.musicState() {
        case let .track(s):
            musicDenied = false
            state = s
            state?.coverHash = coverHash(for: s)
        case .denied:
            musicDenied = true
        case .notPlaying:
            break
        }
        music = state
        let now = Date().timeIntervalSince1970
        if let signal = musicPolicy.signal(for: state, now: now, heartbeatSec: config.heartbeatSec, ttl: config.ttlSec) {
            lastSignal = signal
            Task { await reporter.enqueue(signal) }
        }
    }

    // MARK: 视频

    /// 看视频时往往不碰键盘鼠标，所以空闲久了也照常读；只在暂停上报和休眠时不读
    func sampleVideo() {
        guard !paused, !asleep else { return }
        var state: VideoState?
        switch SystemProbe.safariVideos() {
        case let .tabs(tabs):
            safariDenied = false
            videoBlocked = false
            state = VideoTab.pick(tabs.filter { !config.hides(host: hostOf($0.url)) })
        case .blocked:
            videoBlocked = true
        case .denied:
            safariDenied = true
        case .none:
            break // 没有视频页：沿用上次的判断，打开视频页时再确认
        }
        video = state
        let now = Date().timeIntervalSince1970
        if let signal = videoPolicy.signal(for: state, now: now, heartbeatSec: config.heartbeatSec, ttl: config.ttlSec) {
            lastSignal = signal
            Task { await reporter.enqueue(signal) }
        }
    }

    /// 服务器上已有的封面的哈希；还没上传时启动上传，先返回 nil，上传好后再补发一次
    private func coverHash(for track: MusicState) -> String? {
        if cover?.trackId != track.trackId
            || (cover?.sha256 == nil && Date().timeIntervalSince(cover?.at ?? .distantPast) > 10) {
            let data = SystemProbe.musicArtwork().flatMap { CoverImage.prepare($0) }
            cover = (track.trackId, Date(), data, data.map(CoverImage.sha256))
        }
        guard let data = cover?.data, let hash = cover?.sha256 else { return nil }
        if uploadedCovers.contains(hash) { return hash }
        if coverUploading != hash, Date() >= coverRetryAt {
            coverUploading = hash
            Task {
                let ok = await reporter.uploadCover(data, sha256: hash)
                coverUploading = nil
                if ok {
                    uploadedCovers.insert(hash)
                    sampleMusic()
                } else {
                    coverRetryAt = Date().addingTimeInterval(30) // 服务器连不上时 30 秒后再试
                }
            }
        }
        return nil
    }

    private func projectMatcher() -> ProjectMatcher {
        if Date().timeIntervalSince(matcherScannedAt) > 300 {
            matcher = ProjectMatcher.scan(roots: config.projectRoots)
            matcherScannedAt = Date()
            projectCount = matcher.count
        }
        return matcher
    }

    /// Claude Code / Codex 当前会话所在的项目。
    /// 先按正在看的会话标题反查（切换会话后下一次采样就更新）；读不到标题时退回到最近更新的会话。
    private func agentProject(tool: String, pid: pid_t, windowTitle: String?) -> String? {
        let matcher = projectMatcher()
        switch tool {
        case "claude":
            if let page = SystemProbe.webAreaTitle(pid: pid) {
                // 不是 Claude Code 页面（普通聊天等）就不算项目
                guard page.hasSuffix(" - Claude Code") else { return nil }
                let session = String(page.dropLast(" - Claude Code".count))
                if let folder = claudeFolder(title: session) { return matcher.match(claudeFolder: folder) }
            }
        case "codex":
            if let title = windowTitle, !title.isEmpty {
                let key = "codex:" + title
                if let c = agentCache, c.key == key, Date().timeIntervalSince(c.at) < 30 { return c.project }
                if let cwd = AgentWorkspace.codexCwd(title: title) {
                    let project = matcher.match(path: cwd)
                    agentCache = (key, Date(), project)
                    return project
                }
            }
        default:
            break
        }
        return recentAgentProject(tool: tool)
    }

    /// 会话标题 → 工作区文件夹；找不到时重建索引（最多每 10 秒一次，新会话起名后能很快查到）
    private func claudeFolder(title: String) -> String? {
        if let folder = claudeTitles[title] { return folder }
        guard Date().timeIntervalSince(claudeTitlesAt) > 10 else { return nil }
        claudeTitles = AgentWorkspace.claudeTitleIndex()
        claudeTitlesAt = Date()
        return claudeTitles[title]
    }

    /// 后备：最近更新的会话所在的项目，结果缓存 15 秒，避免每次采样都扫目录
    private func recentAgentProject(tool: String) -> String? {
        let key = "recent:" + tool
        if let c = agentCache, c.key == key, Date().timeIntervalSince(c.at) < 15 { return c.project }
        let since = Date().addingTimeInterval(-config.agentActiveSec)
        var hits: [AgentWorkspace.Hit] = []
        if tool != "codex", let h = AgentWorkspace.latestClaude(since: since) { hits.append(h) }
        if tool != "claude", let h = AgentWorkspace.latestCodex(since: since) { hits.append(h) }
        let matcher = projectMatcher()
        let project = hits.sorted { $0.modified > $1.modified }.lazy.compactMap { hit in
            hit.tool == .claude ? matcher.match(claudeFolder: hit.workspace) : matcher.match(path: hit.workspace)
        }.first
        agentCache = (key, Date(), project)
        return project
    }

    // MARK: 菜单操作

    func pause(hours: Double = 1) {
        pausedUntil = Date().addingTimeInterval(hours * 3600)
        current = nil
        music = nil
        video = nil
    }

    func resume() {
        pausedUntil = nil
        policy.reset()
        musicPolicy.reset()
        videoPolicy.reset()
        sample()
        sampleMusic()
        sampleVideo()
    }

    func save(server: String, token: String, hiddenDomains: String) throws {
        var next = config
        next.server = server.trimmingCharacters(in: .whitespacesAndNewlines)
        next.token = token.trimmingCharacters(in: .whitespacesAndNewlines)
        next.hiddenDomains = AgentConfig.parseDomains(hiddenDomains)
        try ConfigStore.save(next)
        config = next
        matcherScannedAt = .distantPast
        policy.reset()
        musicPolicy.reset()
        videoPolicy.reset()
        coverRetryAt = .distantPast
        Task { await reporter.update(config: next) }
        restartTimer()
        sample()
        sampleMusic()
        sampleVideo()
    }

    // MARK: 显示

    var statusLine: String {
        if paused, let until = pausedUntil {
            return "已暂停上报，\(until.formatted(date: .omitted, time: .shortened)) 恢复"
        }
        guard let s = current else { return "等待采样…" }
        if s.locked { return "已锁屏 / 休眠" }
        if s.idleSec >= config.awaySec { return "空闲 \(Int(s.idleSec / 60)) 分钟" }
        return [s.app, s.project, s.hidden ? "已隐藏的网站" : s.domain].compactMap { $0 }.joined(separator: " · ")
    }

    var musicLine: String? {
        guard let m = music else { return nil }
        let who = m.artist.isEmpty ? "" : " — \(m.artist)"
        return "\(m.playing ? "正在听" : "已暂停")：\(m.title)\(who)\(m.coverHash == nil ? "" : " · 有封面")"
    }

    var videoLine: String? {
        guard let v = video else { return nil }
        let site = v.ref.site == "bilibili" ? "B站" : "YouTube"
        let speed = v.rate == 1 ? "" : " · \(v.rate.formatted())×"
        let name = v.title.isEmpty ? v.ref.id : v.title
        return "\(v.playing ? "正在看" : "已暂停")（\(site)）：\(name)\(speed)"
    }

    var reportLine: String {
        let queued = pendingCount > 0 ? "，待补发 \(pendingCount) 条" : ""
        switch outcome {
        case .idle: return "尚未上报\(queued)"
        case let .sent(date): return "上次上报 \(date.formatted(date: .omitted, time: .standard))\(queued)"
        case let .failed(reason): return "上报失败：\(reason)\(queued)"
        case .unauthorized: return "令牌无效，请在设置里检查\(queued)"
        case .notConfigured: return "尚未配置服务器和令牌\(queued)"
        }
    }

    /// 下拉菜单里展示的最新一条上报：标题行 + 每个字段一行
    var lastSignalLines: [String] {
        guard let s = lastSignal else { return ["最新上报：还没有生成"] }
        let time = Date(timeIntervalSince1970: s.ts).formatted(date: .omitted, time: .standard)
        let delivered = lastDelivered.map { $0.ts >= s.ts } ?? false
        var lines = ["最新上报 \(time) · \(delivered ? "已送达" : "未送达，排队中")"]
        lines.append("kind: \(s.kind)    ttl: \(Int(s.ttl)) 秒")
        for key in s.data.keys.sorted() {
            switch s.data[key]! {
            case let .string(v): lines.append("\(key): \(v)")
            case let .number(v): lines.append("\(key): \(v == v.rounded() ? String(Int(v)) : String(v))")
            case let .bool(v): lines.append("\(key): \(v)")
            }
        }
        return lines
    }

    /// 最新一条上报的完整 JSON（和发给服务器的格式一致）
    var lastSignalJSON: String? {
        guard let s = lastSignal else { return nil }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return (try? encoder.encode(s)).flatMap { String(data: $0, encoding: .utf8) }
    }

    var iconName: String {
        if paused { return "pause.circle" }
        switch outcome {
        case .unauthorized, .notConfigured: return "exclamationmark.triangle"
        case .failed: return "antenna.radiowaves.left.and.right.slash"
        default: return "antenna.radiowaves.left.and.right"
        }
    }
}
