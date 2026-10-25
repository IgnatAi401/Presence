import Foundation
import ImageIO
import Testing
@testable import PresenceAgentCore

@Suite struct ProjectMatcherTests {
    let m = ProjectMatcher(names: ["Presence", "Apex-IgnatAi", "thesis-sim", "server"])

    @Test func vscodeTitle() {
        #expect(m.match(title: "main.py — Presence") == "Presence")
        #expect(m.match(title: "● engine.py — Apex-IgnatAi — Visual Studio Code") == "Apex-IgnatAi")
    }

    @Test func terminalTitle() {
        #expect(m.match(title: "Presence — -zsh — 80×24") == "Presence")
        #expect(m.match(title: "thesis-sim — claude — 120×40") == "thesis-sim")
    }

    @Test func pathTitle() {
        // 从右往左找：server 也是项目名时取 server
        #expect(m.match(title: "~/Developer/Presence/agents/mac") == "Presence")
        #expect(m.match(title: "~/Developer/Presence/server") == "server")
    }

    @Test func caseInsensitiveReturnsFolderName() {
        #expect(m.match(title: "readme.md — presence") == "Presence")
    }

    @Test func noLeakOfOtherTitleContent() {
        #expect(m.match(title: "Secret chat with Alice") == nil)
        #expect(m.match(title: "Presence.md — notes") == nil)
    }

    @Test func scanListsFolders() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root.appendingPathComponent("Alpha"), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: root.appendingPathComponent(".hidden"), withIntermediateDirectories: true)
        try Data().write(to: root.appendingPathComponent("file.txt"))
        defer { try? FileManager.default.removeItem(at: root) }
        let scanned = ProjectMatcher.scan(roots: [root.path])
        #expect(scanned.count == 1)
        #expect(scanned.match(title: "x — Alpha") == "Alpha")
    }
}

@Suite struct HostTests {
    @Test func hosts() {
        #expect(hostOf("https://www.bilibili.com/video/BV1xx?p=2") == "www.bilibili.com")
        #expect(hostOf("http://ArXiv.org/abs/1234") == "arxiv.org")
        #expect(hostOf("favorites://") == nil)
        #expect(hostOf("file:///Users/ignat/a.html") == nil)
        #expect(hostOf("") == nil)
        #expect(hostOf(nil) == nil)
    }
}

@Suite struct SignalTests {
    @Test func desktopSignalEncoding() throws {
        let s = DesktopState(bundleId: "com.microsoft.VSCode", app: "Code", project: "Presence", idleSec: 3.4)
            .signal(ts: 100, ttl: 300)
        let json = try JSONSerialization.jsonObject(with: JSONEncoder().encode(s)) as! [String: Any]
        #expect(json["kind"] as? String == "desktop")
        let data = json["data"] as! [String: Any]
        #expect(data["project"] as? String == "Presence")
        #expect(data["idle_sec"] as? Double == 3)
        #expect(data["domain"] == nil)
        #expect(data["locked"] == nil)
    }

    @Test func policySendsOnChangeAndHeartbeat() {
        var p = ReportPolicy()
        var s = DesktopState(bundleId: "a", app: "A")
        func send(_ now: Double) -> Bool {
            p.shouldSend(s, now: now, heartbeatSec: 120, awaySec: 600)
        }
        #expect(send(0) == true)
        s.idleSec = 30 // 空闲秒数变化本身不触发上报
        #expect(send(5) == false)
        s.domain = "arxiv.org"
        #expect(send(10) == true)
        #expect(send(100) == false)
        #expect(send(130) == true)
        s.idleSec = 601 // 越过离开阈值立即上报
        #expect(send(135) == true)
    }
}

@Suite struct QueueTests {
    @Test func persistsAndTrims() {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).json")
        defer { try? FileManager.default.removeItem(at: url) }
        var q = SignalQueue(fileURL: url, limit: 3)
        for i in 0 ..< 5 {
            q.append(Signal(kind: "heartbeat", ts: Double(i), ttl: 300))
        }
        #expect(q.items.map(\.ts) == [2, 3, 4])
        q.removeFirst(1)
        let reloaded = SignalQueue(fileURL: url, limit: 3)
        #expect(reloaded.items.map(\.ts) == [3, 4])
        #expect(reloaded.peek(1).map(\.ts) == [3])
    }
}

@Suite struct ConfigTests {
    @Test func missingKeysUseDefaults() throws {
        let c = try JSONDecoder().decode(AgentConfig.self, from: Data(#"{"token":"abc"}"#.utf8))
        #expect(c.token == "abc")
        #expect(c.heartbeatSec == 120)
        #expect(c.signalURL?.absoluteString == "https://api.ignat.ai/presence/api/signal")
    }
}

@Suite struct AgentWorkspaceTests {
    let m = ProjectMatcher(names: ["Presence", "dsh-telegram", "dsh", "server"], roots: ["/Users/me/Developer"])

    @Test func claudeFolderMatchesLongestProject() {
        #expect(m.match(claudeFolder: "-Users-me-Developer-Presence") == "Presence")
        #expect(m.match(claudeFolder: "-Users-me-Developer-Presence-agents-mac") == "Presence")
        // dsh 和 dsh-telegram 都能对上前缀时取更长的
        #expect(m.match(claudeFolder: "-Users-me-Developer-dsh-telegram") == "dsh-telegram")
        #expect(m.match(claudeFolder: "-Users-me-Developer-dsh-web") == "dsh")
        #expect(m.match(claudeFolder: "-Users-me-Documents-Presence") == nil)
    }

    @Test func pathUsesFirstFolderUnderRoot() {
        // 项目里的 server 子目录不能盖过项目本身
        #expect(m.match(path: "/Users/me/Developer/Presence/server") == "Presence")
        #expect(m.match(path: "/Users/me/.codex/worktrees/ab12/Presence") == "Presence")
        #expect(m.match(path: "/Users/me/Documents/notes") == nil)
    }

    @Test func latestSessions() throws {
        let fm = FileManager.default
        let root = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? fm.removeItem(at: root) }
        let now = Date()

        let claude = root.appendingPathComponent("claude")
        for (dir, age) in [("-Users-me-Developer-old", 3000.0), ("-Users-me-Developer-Presence", 10)] {
            let d = claude.appendingPathComponent(dir)
            try fm.createDirectory(at: d, withIntermediateDirectories: true)
            let f = d.appendingPathComponent("s.jsonl")
            try Data("{}\n".utf8).write(to: f)
            try fm.setAttributes([.modificationDate: now.addingTimeInterval(-age)], ofItemAtPath: f.path)
        }
        #expect(AgentWorkspace.latestClaude(root: claude, since: now.addingTimeInterval(-60))?.workspace
            == "-Users-me-Developer-Presence")
        #expect(AgentWorkspace.latestClaude(root: claude, since: now.addingTimeInterval(1)) == nil)

        let c = Calendar.current.dateComponents([.year, .month, .day], from: now)
        let day = root.appendingPathComponent(String(format: "codex/%04d/%02d/%02d", c.year!, c.month!, c.day!))
        try fm.createDirectory(at: day, withIntermediateDirectories: true)
        let meta = #"{"type":"session_meta","payload":{"id":"x","cwd":"/Users/me/Developer/Presence","instructions":"long"}}"#
        try Data((meta + "\n{}\n").utf8).write(to: day.appendingPathComponent("rollout-1.jsonl"))
        let hit = AgentWorkspace.latestCodex(root: root.appendingPathComponent("codex"), since: now.addingTimeInterval(-60), now: now)
        #expect(hit?.workspace == "/Users/me/Developer/Presence")
    }
}

@Suite struct AgentTitleTests {
    @Test func claudeTitleIndex() throws {
        let fm = FileManager.default
        let root = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? fm.removeItem(at: root) }
        for (folder, session, title) in [("-Users-me-Developer-Presence", "s1", "修 bug"), ("-Users-me-Developer-Alpha", "s2", "写文档")] {
            let d = root.appendingPathComponent("\(folder)/\(session)")
            try fm.createDirectory(at: d, withIntermediateDirectories: true)
            try Data(#"{"customTitle":"\#(title)"}"#.utf8).write(to: d.appendingPathComponent("custom-title.json"))
        }
        let index = AgentWorkspace.claudeTitleIndex(root: root)
        #expect(index["修 bug"] == "-Users-me-Developer-Presence")
        #expect(index["写文档"] == "-Users-me-Developer-Alpha")
        #expect(index["别的"] == nil)
    }

    @Test func codexCwdByTitle() throws {
        let path = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".sqlite")
        defer { try? FileManager.default.removeItem(at: path) }
        let sql = """
        CREATE TABLE threads (id TEXT, cwd TEXT, title TEXT, name TEXT, updated_at INTEGER);
        INSERT INTO threads VALUES ('a', '/Users/me/Developer/Old', '测试性能', NULL, 1);
        INSERT INTO threads VALUES ('b', '/Users/me/Developer/Presence', '很长的首条消息', '测试性能', 2);
        """
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/sqlite3")
        p.arguments = [path.path, sql]
        try p.run()
        p.waitUntilExit()
        #expect(AgentWorkspace.codexCwd(title: "测试性能", database: path) == "/Users/me/Developer/Presence")
        #expect(AgentWorkspace.codexCwd(title: "没有", database: path) == nil)
    }
}

@Suite struct MusicPolicyTests {
    let a = MusicState(trackId: "A1", title: "A", artist: "x", album: "y", duration: 200, position: 10, playing: true)

    @Test func signalFields() {
        var s = a
        s.coverHash = "ab12"
        let sig = s.signal(ts: 100, ttl: 300)
        #expect(sig.kind == "music" && sig.ttl == 300)
        #expect(sig.data["player"] == .string("apple_music"))
        #expect(sig.data["state"] == .string("playing"))
        #expect(sig.data["position"] == .number(10) && sig.data["duration"] == .number(200))
        #expect(sig.data["cover_hash"] == .string("ab12"))
        #expect(a.signal(ts: 100, ttl: 300).data["cover_hash"] == nil)
    }

    @Test func sendsOnChangesAndHeartbeat() {
        var p = MusicPolicy()
        #expect(p.signal(for: a, now: 0, heartbeatSec: 120, ttl: 300) != nil)
        var s = a
        s.position = 15 // 正常往前走，不发
        #expect(p.signal(for: s, now: 5, heartbeatSec: 120, ttl: 300) == nil)
        s.position = 80 // 拖动进度
        #expect(p.signal(for: s, now: 10, heartbeatSec: 120, ttl: 300) != nil)
        s.position = 200
        s.trackId = "B1"
        s.title = "B" // 换歌
        #expect(p.signal(for: s, now: 12, heartbeatSec: 120, ttl: 300) != nil)
        s.coverHash = "cd34" // 封面传好了
        #expect(p.signal(for: s, now: 13, heartbeatSec: 120, ttl: 300)?.data["cover_hash"] == .string("cd34"))
        s.position = 200 + 120
        #expect(p.signal(for: s, now: 133, heartbeatSec: 120, ttl: 300) != nil) // 心跳
    }

    @Test func pauseSentOnceThenQuiet() {
        var p = MusicPolicy()
        _ = p.signal(for: a, now: 0, heartbeatSec: 120, ttl: 300)
        var s = a
        s.position = 30
        s.playing = false
        #expect(p.signal(for: s, now: 20, heartbeatSec: 120, ttl: 300)?.data["state"] == .string("paused"))
        #expect(p.signal(for: s, now: 500, heartbeatSec: 120, ttl: 300) == nil) // 暂停中不发心跳
        s.playing = true
        #expect(p.signal(for: s, now: 510, heartbeatSec: 120, ttl: 300)?.data["state"] == .string("playing"))
    }

    @Test func quitWhilePlayingSendsPausedOnce() {
        var p = MusicPolicy()
        _ = p.signal(for: a, now: 0, heartbeatSec: 120, ttl: 300)
        let sig = p.signal(for: nil, now: 20, heartbeatSec: 120, ttl: 300)
        #expect(sig?.data["state"] == .string("paused"))
        #expect(sig?.data["position"] == .number(30))
        #expect(p.signal(for: nil, now: 30, heartbeatSec: 120, ttl: 300) == nil)
        var q = MusicPolicy()
        #expect(q.signal(for: nil, now: 0, heartbeatSec: 120, ttl: 300) == nil) // 一直没在放
    }
}

@Suite struct HiddenDomainTests {
    @Test func parseAndMatch() {
        let domains = AgentConfig.parseDomains("zhihu.com, https://www.Weibo.com/u/1\n  xiaohongshu.com；bad zhihu.com")
        #expect(domains == ["zhihu.com", "weibo.com", "xiaohongshu.com"])
        var c = AgentConfig()
        c.hiddenDomains = domains
        #expect(c.hides(host: "www.zhihu.com") && c.hides(host: "zhihu.com") && c.hides(host: "WEIBO.COM"))
        #expect(!c.hides(host: "notzhihu.com") && !c.hides(host: nil))
    }

    @Test func desktopSignalTitleAndHidden() {
        var s = DesktopState(bundleId: "com.apple.Safari", app: "Safari", domain: "arxiv.org",
                             title: String(repeating: "论", count: 300))
        #expect(s.signal(ts: 0, ttl: 300).data["title"] == .string(String(repeating: "论", count: 200)))
        let key = s.changeKey(awaySec: 600)
        s.title = "另一篇"
        #expect(s.changeKey(awaySec: 600) != key) // 换网页立即上报
        let hidden = DesktopState(bundleId: "com.apple.Safari", app: "Safari", hidden: true)
        let data = hidden.signal(ts: 0, ttl: 300).data
        #expect(data["hidden"] == .bool(true) && data["domain"] == nil && data["title"] == nil)
    }
}

@Suite struct VideoTests {
    @Test func parseBilibili() {
        #expect(VideoRef.parse("https://www.bilibili.com/video/BV1xx411c7mD/?spm_id_from=333")
            == VideoRef(site: "bilibili", id: "BV1xx411c7mD"))
        #expect(VideoRef.parse("https://www.bilibili.com/video/BV1xx411c7mD?p=3")?.page == 3)
        #expect(VideoRef.parse("https://www.bilibili.com/video/BV1xx411c7mD?p=1")?.page == nil) // 第 1 P 不记
        #expect(VideoRef.parse("https://www.bilibili.com/bangumi/play/ep12345")?.id == "ep12345")
        #expect(VideoRef.parse("https://m.bilibili.com/video/BV1xx411c7mD")?.site == "bilibili")
        #expect(VideoRef.parse("https://www.bilibili.com/") == nil)
        #expect(VideoRef.parse("https://space.bilibili.com/123") == nil)
    }

    @Test func parseYouTube() {
        #expect(VideoRef.parse("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s")
            == VideoRef(site: "youtube", id: "dQw4w9WgXcQ"))
        #expect(VideoRef.parse("https://www.youtube.com/shorts/abc123")?.id == "abc123")
        #expect(VideoRef.parse("https://www.youtube.com/watch") == nil)
        #expect(VideoRef.parse("https://www.youtube.com/@channel") == nil)
        #expect(VideoRef.parse("https://notyoutube.com/watch?v=x") == nil)
        #expect(VideoRef.parse(nil) == nil)
    }

    @Test func probeParsing() {
        let tab = VideoTab(url: "https://www.youtube.com/watch?v=A", title: "t", probe: "1,12.34,300,1.5", front: true)
        #expect(tab.state == VideoState(ref: VideoRef(site: "youtube", id: "A"), title: "t", duration: 300,
                                        position: 12.34, playing: true, rate: 1.5))
        var empty = tab
        empty.probe = "" // 页面还没加载出 <video>
        #expect(empty.state == nil)
        var live = tab
        live.probe = "1,50,0,1" // 直播没有时长
        #expect(live.state?.duration == 0)
    }

    @Test func probeReadsComments() {
        let tab = VideoTab(url: "https://www.bilibili.com/video/BV1", title: "t", probe: "0,600,600,1,1", front: true)
        #expect(tab.state?.viewing == .comments)
        #expect(tab.state?.active == true) // 看完了在看评论，仍算在看
        var back = tab
        back.front = false // 后台标签页的滚动位置不算
        #expect(back.state?.viewing == .video)
        var top = tab
        top.probe = "1,10,600,1,0"
        #expect(top.state?.viewing == .video)
        #expect(tab.state?.signal(ts: 0, ttl: 300).data["viewing"] == .string("comments"))
        #expect(top.state?.signal(ts: 0, ttl: 300).data["viewing"] == nil)
    }

    @Test func viewingNeedsTwoSamples() {
        var d = ViewingDebounce()
        var v = VideoState(ref: VideoRef(site: "bilibili", id: "BV1"), playing: false, viewing: .comments)
        #expect(d.apply(v)?.viewing == .video) // 第一次只记下
        #expect(d.apply(v)?.viewing == .comments) // 连续两次才切换
        v.viewing = .video
        #expect(d.apply(v)?.viewing == .comments) // 滚回去一下不马上变
        v.viewing = .comments
        #expect(d.apply(v)?.viewing == .comments)
        v.ref.id = "BV2" // 换视频从头开始
        #expect(d.apply(v)?.viewing == .video)
        #expect(d.apply(nil) == nil)
    }

    @Test func policyKeepsCommentsAlive() {
        var p = PlaybackPolicy<VideoState>()
        var v = VideoState(ref: VideoRef(site: "bilibili", id: "BV1"), duration: 600, position: 600, playing: false)
        #expect(p.signal(for: v, now: 0, heartbeatSec: 120, ttl: 300) != nil)
        v.viewing = .comments // 切到评论区立即发
        #expect(p.signal(for: v, now: 5, heartbeatSec: 120, ttl: 300)?.data["viewing"] == .string("comments"))
        #expect(p.signal(for: v, now: 130, heartbeatSec: 120, ttl: 300) != nil) // 暂停着看评论也有心跳
        let end = p.signal(for: nil, now: 140, heartbeatSec: 120, ttl: 300) // 关掉页面补一条结束
        #expect(end?.data["state"] == .string("paused") && end?.data["viewing"] == nil)
        #expect(end?.data["position"] == .number(600))
    }

    @Test func pickPrefersPlaying() {
        func tab(_ id: String, playing: Bool, front: Bool) -> VideoTab {
            VideoTab(url: "https://www.bilibili.com/video/\(id)", title: id, probe: "\(playing ? 1 : 0),1,100,1",
                     front: front)
        }
        #expect(VideoTab.pick([tab("BV1", playing: false, front: true), tab("BV2", playing: true, front: false)])?
            .ref.id == "BV2")
        #expect(VideoTab.pick([tab("BV1", playing: true, front: false), tab("BV2", playing: true, front: true)])?
            .ref.id == "BV2")
        #expect(VideoTab.pick([tab("BV1", playing: false, front: true)])?.playing == false)
        #expect(VideoTab.pick([tab("BV1", playing: false, front: false)]) == nil) // 后台暂停的不算在看
        #expect(VideoTab.pick([]) == nil)
    }

    @Test func signalFields() {
        let v = VideoState(ref: VideoRef(site: "bilibili", id: "BV1", page: 2), duration: 600, position: 61.26,
                           playing: true, rate: 2)
        let sig = v.signal(ts: 100, ttl: 300)
        #expect(sig.kind == "video")
        #expect(sig.data["site"] == .string("bilibili") && sig.data["id"] == .string("BV1"))
        #expect(sig.data["page"] == .number(2) && sig.data["rate"] == .number(2))
        #expect(sig.data["position"] == .number(61.3) && sig.data["state"] == .string("playing"))
        var normal = v
        normal.rate = 1
        normal.ref.page = nil
        #expect(normal.signal(ts: 100, ttl: 300).data["rate"] == nil)
        #expect(normal.signal(ts: 100, ttl: 300).data["page"] == nil)
    }

    @Test func policyFollowsPlaybackRate() {
        var p = PlaybackPolicy<VideoState>()
        var v = VideoState(ref: VideoRef(site: "bilibili", id: "BV1"), duration: 600, position: 0, playing: true, rate: 2)
        #expect(p.signal(for: v, now: 0, heartbeatSec: 120, ttl: 300) != nil)
        v.position = 10 // 2 倍速 5 秒走了 10 秒，不算拖动
        #expect(p.signal(for: v, now: 5, heartbeatSec: 120, ttl: 300) == nil)
        v.rate = 1 // 改倍速立即发
        #expect(p.signal(for: v, now: 6, heartbeatSec: 120, ttl: 300) != nil)
        v.ref.page = 2 // 换分 P
        v.position = 0
        #expect(p.signal(for: v, now: 7, heartbeatSec: 120, ttl: 300)?.data["page"] == .number(2))
        v.title = "（1）新标题" // 标题变化不发
        v.position = 3
        #expect(p.signal(for: v, now: 10, heartbeatSec: 120, ttl: 300) == nil)
        #expect(p.signal(for: nil, now: 20, heartbeatSec: 120, ttl: 300)?.data["state"] == .string("paused"))
    }
}

@Suite struct CoverImageTests {
    @Test func mimeSniffing() {
        #expect(CoverImage.mimeType(of: Data([0xFF, 0xD8, 0xFF, 0xE0])) == "image/jpeg")
        #expect(CoverImage.mimeType(of: Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0])) == "image/png")
        #expect(CoverImage.mimeType(of: Data("RIFF\0\0\0\0WEBPVP8 ".utf8)) == "image/webp")
        #expect(CoverImage.mimeType(of: Data("GIF89a".utf8)) == nil)
    }

    @Test func sha256Hex() {
        #expect(CoverImage.sha256(Data("abc".utf8))
            == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    }

    @Test func prepareKeepsSmallAndShrinksLarge() throws {
        let small = Data([0xFF, 0xD8, 0xFF, 0xE0, 1, 2, 3])
        #expect(CoverImage.prepare(small) == small)
        #expect(CoverImage.prepare(Data("not an image".utf8)) == nil)

        // 生成一张 1200×1200 的 TIFF（服务器不收的格式），应被缩小并转成 JPEG
        let ctx = try #require(CGContext(data: nil, width: 1200, height: 1200, bitsPerComponent: 8, bytesPerRow: 0,
                                         space: CGColorSpaceCreateDeviceRGB(),
                                         bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
        ctx.setFillColor(red: 0.2, green: 0.5, blue: 0.8, alpha: 1)
        ctx.fill(CGRect(x: 0, y: 0, width: 1200, height: 1200))
        let tiff = NSMutableData()
        let dest = try #require(CGImageDestinationCreateWithData(tiff, "public.tiff" as CFString, 1, nil))
        CGImageDestinationAddImage(dest, try #require(ctx.makeImage()), nil)
        #expect(CGImageDestinationFinalize(dest))

        let out = try #require(CoverImage.prepare(tiff as Data))
        #expect(CoverImage.mimeType(of: out) == "image/jpeg")
        let src = try #require(CGImageSourceCreateWithData(out as CFData, nil))
        let img = try #require(CGImageSourceCreateImageAtIndex(src, 0, nil))
        #expect(max(img.width, img.height) == 600)
    }
}

@Suite struct MediaURLTests {
    @Test func mediaURL() {
        var c = AgentConfig()
        c.server = "http://127.0.0.1:8790/presence/api/"
        #expect(c.mediaURL("ab")?.absoluteString == "http://127.0.0.1:8790/presence/api/media/ab")
        #expect(c.signalURL?.absoluteString == "http://127.0.0.1:8790/presence/api/signal")
    }
}
