import Foundation
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
