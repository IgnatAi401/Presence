import Foundation

/// 从窗口标题里找出项目名。
///
/// 只认 projectRoots 下已有的文件夹名，所以上传的只会是项目名，不会是文件名、命令或其他标题内容。
public struct ProjectMatcher: Sendable {
    private let names: [String: String] // 小写 → 文件夹原名
    private let roots: [String] // 展开后的根目录绝对路径

    public init(names: [String], roots: [String] = []) {
        var map: [String: String] = [:]
        for n in names where !n.isEmpty {
            map[n.lowercased()] = n
        }
        self.names = map
        self.roots = roots.map { ($0 as NSString).expandingTildeInPath }
    }

    /// 列出各根目录下的子文件夹（不含隐藏文件夹）。
    public static func scan(roots: [String], fileManager: FileManager = .default) -> ProjectMatcher {
        var found: [String] = []
        for root in roots {
            let path = (root as NSString).expandingTildeInPath
            guard let items = try? fileManager.contentsOfDirectory(
                at: URL(fileURLWithPath: path, isDirectory: true),
                includingPropertiesForKeys: [.isDirectoryKey],
                options: [.skipsHiddenFiles]
            ) else { continue }
            for item in items where (try? item.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true {
                found.append(item.lastPathComponent)
            }
        }
        return ProjectMatcher(names: found, roots: roots)
    }

    public var count: Int { names.count }

    /// 所有项目文件夹名，按名称排序（设置里列出来选分类）
    public var allNames: [String] {
        names.values.sorted { $0.localizedStandardCompare($1) == .orderedAscending }
    }

    private static let separators = [" — ", " – ", " - ", " | ", " · ", ": "]

    /// 从右往左找第一个和项目名完全一致的片段。
    /// 例如 "main.py — Presence"、"Presence — -zsh — 80×24"、"~/Developer/Presence/server"。
    public func match(title: String) -> String? {
        var parts = [title]
        for sep in Self.separators {
            parts = parts.flatMap { $0.components(separatedBy: sep) }
        }
        for part in parts.reversed() {
            let trimmed = part.trimmingCharacters(in: .whitespacesAndNewlines)
            if let hit = names[trimmed.lowercased()] {
                return hit
            }
            // 路径或带空格的片段再细分一次
            let tokens = trimmed.split(whereSeparator: { $0 == "/" || $0 == "\\" || $0.isWhitespace })
            for token in tokens.reversed() {
                if let hit = names[token.lowercased()] {
                    return hit
                }
            }
        }
        return nil
    }

    /// 从工作区路径里找项目名：根目录下的第一级文件夹，例如 ~/Developer/Presence/agents/mac → Presence。
    /// 不在根目录下的（比如 Codex 的 worktree）退回到从右往左找和项目名一致的路径片段。
    public func match(path: String) -> String? {
        let path = (path as NSString).expandingTildeInPath
        for root in roots where path.hasPrefix(root + "/") {
            let first = path.dropFirst(root.count + 1).split(separator: "/").first.map(String.init) ?? ""
            if let hit = names[first.lowercased()] { return hit }
        }
        for part in path.split(separator: "/").reversed() {
            if let hit = names[part.lowercased()] { return hit }
        }
        return nil
    }

    /// 从 Claude Code 的工作区文件夹名（路径编码后）里找项目名。
    /// 编码会丢失信息（"/" 和 "-" 都变成 "-"），所以按根目录前缀 + 已知项目名比对，取最长的匹配。
    public func match(claudeFolder folder: String) -> String? {
        var best: String?
        for root in roots {
            let prefix = AgentWorkspace.claudeEncode(root) + "-"
            guard folder.hasPrefix(prefix) else { continue }
            let rest = folder.dropFirst(prefix.count).lowercased()
            for (key, name) in names {
                let encoded = AgentWorkspace.claudeEncode(key)
                if rest == encoded || rest.hasPrefix(encoded + "-"), encoded.count > (best?.count ?? 0) {
                    best = name
                }
            }
        }
        return best
    }
}

/// 从网址里只取域名；非 http(s) 网址（起始页、本地文件等）返回 nil。
public func hostOf(_ urlString: String?) -> String? {
    guard let s = urlString, let url = URL(string: s),
          let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https",
          let host = url.host, !host.isEmpty
    else { return nil }
    return host.lowercased()
}
