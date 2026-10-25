import Foundation

/// 网址里的视频身份：B站 BV 号（或番剧 ep / ss 号）和分 P，YouTube 视频 ID。
public struct VideoRef: Equatable, Sendable {
    public var site: String
    public var id: String
    /// B站分 P，第 1 P 记为 nil（网址里有没有 ?p=1 都算同一个视频）
    public var page: Int?

    public init(site: String, id: String, page: Int? = nil) {
        self.site = site
        self.id = id
        self.page = page
    }

    /// 不是 B站 / YouTube 的视频页时返回 nil
    public static func parse(_ urlString: String?) -> VideoRef? {
        guard let host = hostOf(urlString), let comps = URLComponents(string: urlString!) else { return nil }
        let path = comps.path.split(separator: "/").map(String.init)
        let query = { (name: String) in comps.queryItems?.first { $0.name == name }?.value }
        if host == "bilibili.com" || host.hasSuffix(".bilibili.com") {
            var id: String?
            if path.count >= 2, path[0] == "video" {
                id = path[1]
            } else if path.count >= 3, path[0] == "bangumi", path[1] == "play" {
                id = path[2]
            }
            guard let id, !id.isEmpty else { return nil }
            let page = query("p").flatMap(Int.init).flatMap { $0 > 1 ? $0 : nil }
            return VideoRef(site: "bilibili", id: id, page: page)
        }
        if host == "youtube.com" || host.hasSuffix(".youtube.com") {
            var id: String?
            if path == ["watch"] {
                id = query("v")
            } else if path.count >= 2, path[0] == "shorts" || path[0] == "live" {
                id = path[1]
            }
            guard let id, !id.isEmpty else { return nil }
            return VideoRef(site: "youtube", id: id)
        }
        return nil
    }
}

/// 页面上在看哪里：视频本身，还是视频已滚出画面、在看下面的评论区。
public enum VideoViewing: String, Equatable, Sendable {
    case video
    case comments
}

/// Safari 里某个视频页的播放状态。
public struct VideoState: PlaybackState {
    public var ref: VideoRef
    /// 标签页标题，只在菜单里显示，不上报（标题由服务器按视频 ID 补全）
    public var title: String
    public var duration: Double
    public var position: Double
    public var playing: Bool
    /// 播放倍速（B站常开 1.5 / 2 倍），用来推算进度
    public var rate: Double
    public var viewing: VideoViewing
    public var isPrivate: Bool = false

    public init(ref: VideoRef, title: String = "", duration: Double = 0, position: Double = 0,
                playing: Bool, rate: Double = 1, viewing: VideoViewing = .video) {
        self.ref = ref
        self.title = title
        self.duration = duration
        self.position = position
        self.playing = playing
        self.rate = rate
        self.viewing = viewing
    }

    /// 换视频、换分 P、切到评论区时立即上报；标签页标题会变（YouTube 有未读数），不算
    public var changeKey: String {
        [ref.site, ref.id, ref.page.map(String.init) ?? "", viewing.rawValue, isPrivate ? "private" : ""].joined(separator: "\u{1F}")
    }

    /// 暂停着但在看评论区，也算还在看这个视频
    public var active: Bool { playing || viewing == .comments }

    public mutating func stop() {
        playing = false
        viewing = .video
    }

    public func signal(ts: Double, ttl: Double) -> Signal {
        var data: [String: JSONValue] = [
            "site": .string(ref.site),
            "id": .string(ref.id),
            "state": .string(playing ? "playing" : "paused"),
            "position": .number((position * 10).rounded() / 10),
            "duration": .number((duration * 10).rounded() / 10),
        ]
        if isPrivate { data["private"] = .bool(true) }
        if let page = ref.page { data["page"] = .number(Double(page)) }
        if rate != 1 { data["rate"] = .number(rate) }
        if viewing == .comments { data["viewing"] = .string(viewing.rawValue) }
        return Signal(kind: "video", ts: ts, ttl: ttl, data: data)
    }
}

/// 一个视频页标签页：网址、标题，以及页面脚本的返回值。
public struct VideoTab: Equatable, Sendable {
    public var url: String
    public var title: String
    /// 页面脚本的返回值："播放中(1/0),进度,时长,倍速,在看评论(1/0)"；页面上还没有 <video> 时为空字符串
    public var probe: String
    /// 是否是最前面窗口的当前标签页
    public var front: Bool

    public init(url: String, title: String, probe: String, front: Bool) {
        self.url = url
        self.title = title
        self.probe = probe
        self.front = front
    }

    public var state: VideoState? {
        guard let ref = VideoRef.parse(url) else { return nil }
        let f = probe.split(separator: ",").map { Double($0) }
        guard f.count == 4 || f.count == 5, let playing = f[0], let position = f[1], let duration = f[2],
              let rate = f[3], position.isFinite, duration.isFinite
        else { return nil }
        // 只有最前面的标签页才可能正在被看着，后台标签页的滚动位置没有意义
        let comments = front && f.count == 5 && f[4] == 1
        return VideoState(ref: ref, title: title, duration: max(0, duration), position: max(0, position),
                          playing: playing == 1, rate: rate > 0 && rate.isFinite ? rate : 1,
                          viewing: comments ? .comments : .video)
    }

    /// 选出要上报的视频：正在播放的优先（最前面的标签页再优先）；都没在放时只看最前面的标签页。
    /// 后台标签页里暂停着的视频不算在看。
    public static func pick(_ tabs: [VideoTab], isPrivate: (VideoTab) -> Bool = { _ in false }) -> VideoState? {
        let states = tabs.compactMap { tab in tab.state.map {
            var state = $0
            state.isPrivate = isPrivate(tab)
            return (tab.front, state)
        } }
        return states.first { $0.0 && $0.1.playing }?.1
            ?? states.first { $0.1.playing }?.1
            ?? states.first { $0.0 }?.1
    }
}

/// 视频和评论区之间来回切换要连续确认两次（约 10 秒）才算数，稍微往下滚一下不会马上变成“在看评论”。
public struct ViewingDebounce: Sendable {
    public private(set) var confirmed: VideoViewing = .video
    private var candidate: VideoViewing?
    private var key: String?

    public init() {}

    public mutating func apply(_ state: VideoState?) -> VideoState? {
        guard var state else {
            reset()
            return nil
        }
        let id = [state.ref.site, state.ref.id, state.ref.page.map(String.init) ?? ""].joined(separator: "\u{1F}")
        if id != key {
            // 换了视频：重新从“在看视频”开始
            key = id
            confirmed = .video
            candidate = nil
        }
        if state.viewing == confirmed {
            candidate = nil
        } else if candidate == state.viewing {
            confirmed = state.viewing
            candidate = nil
        } else {
            candidate = state.viewing
        }
        state.viewing = confirmed
        return state
    }

    public mutating func reset() {
        confirmed = .video
        candidate = nil
        key = nil
    }
}
