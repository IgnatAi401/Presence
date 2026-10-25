import Foundation
import SQLite3

/// 从 Claude Code 和 Codex 的本地会话记录里找出最近在用的工作区。
///
/// 两者的窗口标题里通常没有项目名，但会把每个会话写成 .jsonl 文件，正在进行的会话会不断更新：
/// - Claude Code：~/.claude/projects/<工作区路径编码>/<会话>.jsonl，文件夹名是把路径里非字母数字的字符换成 "-"
/// - Codex：~/.codex/sessions/年/月/日/rollout-*.jsonl，第一行 session_meta 里有 cwd
/// 只读文件夹名、修改时间和 Codex 文件开头的 cwd，不读对话内容。
///
/// 更准的办法是按“正在看的会话标题”反查（见 claudeTitleIndex / codexCwd(title:)）：切换会话后立刻生效，
/// 不用等会话文件被写入。按修改时间找最近的会话只作为读不到标题时的后备。
public enum AgentWorkspace {
    public enum Tool: String, Sendable {
        case claude
        case codex
    }

    public struct Hit: Equatable, Sendable {
        public var tool: Tool
        /// Claude Code 是编码后的文件夹名，Codex 是真实路径
        public var workspace: String
        public var modified: Date
    }

    public static var claudeRoot: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".claude/projects")
    }

    public static var codexRoot: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex/sessions")
    }

    /// Claude Code 最近更新的会话所在的工作区文件夹名
    public static func latestClaude(root: URL = claudeRoot, since: Date, fileManager: FileManager = .default) -> Hit? {
        var best: Hit?
        for dir in subdirectories(of: root, fileManager: fileManager) {
            guard let date = newestFile(in: dir, ext: "jsonl", fileManager: fileManager)?.1, date >= since else { continue }
            if best.map({ date > $0.modified }) ?? true {
                best = Hit(tool: .claude, workspace: dir.lastPathComponent, modified: date)
            }
        }
        return best
    }

    /// Codex 最近更新的会话的工作区路径。只看今天和前两天的文件夹，不用遍历全部历史。
    public static func latestCodex(root: URL = codexRoot, since: Date, now: Date = Date(),
                                   fileManager: FileManager = .default) -> Hit? {
        let calendar = Calendar.current
        var best: (URL, Date)?
        for offset in 0 ... 2 {
            guard let day = calendar.date(byAdding: .day, value: -offset, to: now) else { continue }
            let c = calendar.dateComponents([.year, .month, .day], from: day)
            let dir = root.appendingPathComponent(String(format: "%04d/%02d/%02d", c.year!, c.month!, c.day!))
            if let hit = newestFile(in: dir, ext: "jsonl", fileManager: fileManager), hit.1 >= since,
               best.map({ hit.1 > $0.1 }) ?? true {
                best = hit
            }
        }
        guard let (file, date) = best, let cwd = codexCwd(file: file) else { return nil }
        return Hit(tool: .codex, workspace: cwd, modified: date)
    }

    /// 读 Codex 会话文件开头，取出 session_meta 里的 cwd（第一行可能很长，只读前 64 KB 用正则找）
    static func codexCwd(file: URL) -> String? {
        guard let handle = try? FileHandle(forReadingFrom: file) else { return nil }
        defer { try? handle.close() }
        guard let data = try? handle.read(upToCount: 64 * 1024),
              let text = String(data: data, encoding: .utf8) ?? String(data: data.dropLast(4), encoding: .utf8),
              let line = text.split(separator: "\n", maxSplits: 1).first,
              let range = line.range(of: #""cwd"\s*:\s*"((?:[^"\\]|\\.)*)""#, options: .regularExpression)
        else { return nil }
        // 把匹配到的 "cwd":"..." 当 JSON 解析，顺便处理转义
        let json = "{" + line[range] + "}"
        return (try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: String])?["cwd"]
    }

    /// Claude Code 会话标题 → 工作区文件夹名。标题存在 <工作区>/<会话>/custom-title.json；
    /// 同名标题取最近更新的会话。
    public static func claudeTitleIndex(root: URL = claudeRoot, fileManager: FileManager = .default) -> [String: String] {
        var index: [String: (String, Date)] = [:]
        for dir in subdirectories(of: root, fileManager: fileManager) {
            for session in subdirectories(of: dir, fileManager: fileManager) {
                let file = session.appendingPathComponent("custom-title.json")
                guard let data = try? Data(contentsOf: file),
                      let title = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["customTitle"] as? String,
                      !title.isEmpty
                else { continue }
                let date = (try? file.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                if index[title].map({ date > $0.1 }) ?? true {
                    index[title] = (dir.lastPathComponent, date)
                }
            }
        }
        return index.mapValues { $0.0 }
    }

    public static var codexDatabase: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex/state_5.sqlite")
    }

    /// Codex 对话标题（窗口标题）→ 工作区路径，查 Codex 自己的状态库（只读）
    public static func codexCwd(title: String, database: URL = codexDatabase) -> String? {
        var db: OpaquePointer?
        guard sqlite3_open_v2(database.path, &db, SQLITE_OPEN_READONLY, nil) == SQLITE_OK else {
            sqlite3_close(db)
            return nil
        }
        defer { sqlite3_close(db) }
        sqlite3_busy_timeout(db, 200)
        var stmt: OpaquePointer?
        let sql = "SELECT cwd FROM threads WHERE name = ?1 OR title = ?1 ORDER BY updated_at DESC LIMIT 1"
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return nil }
        defer { sqlite3_finalize(stmt) }
        let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        sqlite3_bind_text(stmt, 1, title, -1, transient)
        guard sqlite3_step(stmt) == SQLITE_ROW, let text = sqlite3_column_text(stmt, 0) else { return nil }
        return String(cString: text)
    }

    /// Claude Code 给工作区路径起文件夹名的方式：非字母数字的字符都换成 "-"
    public static func claudeEncode(_ path: String) -> String {
        String(path.map { $0.isASCII && ($0.isLetter || $0.isNumber) ? $0 : "-" })
    }

    private static func subdirectories(of url: URL, fileManager: FileManager) -> [URL] {
        let items = (try? fileManager.contentsOfDirectory(
            at: url, includingPropertiesForKeys: [.isDirectoryKey], options: [.skipsHiddenFiles]
        )) ?? []
        return items.filter { (try? $0.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true }
    }

    private static func newestFile(in dir: URL, ext: String, fileManager: FileManager) -> (URL, Date)? {
        let items = (try? fileManager.contentsOfDirectory(
            at: dir, includingPropertiesForKeys: [.contentModificationDateKey], options: [.skipsHiddenFiles]
        )) ?? []
        var best: (URL, Date)?
        for item in items where item.pathExtension == ext {
            guard let date = try? item.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate
            else { continue }
            if best.map({ date > $0.1 }) ?? true { best = (item, date) }
        }
        return best
    }
}
