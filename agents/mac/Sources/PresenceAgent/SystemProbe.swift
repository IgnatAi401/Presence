import AppKit
import ApplicationServices
import IOKit
import PresenceAgentCore

/// 读取系统信息：前台 App、窗口标题（辅助功能）、Safari 当前网址和“音乐”当前曲目（自动化）、空闲时间（IOKit）。
@MainActor
enum SystemProbe {
    static let safariBundleId = "com.apple.Safari"

    struct FrontApp {
        let bundleId: String
        let name: String
        let pid: pid_t
    }

    static func frontApp() -> FrontApp? {
        guard let app = NSWorkspace.shared.frontmostApplication, let bundleId = app.bundleIdentifier else {
            return nil
        }
        return FrontApp(bundleId: bundleId, name: app.localizedName ?? bundleId, pid: app.processIdentifier)
    }

    // MARK: 辅助功能：焦点窗口标题

    static var accessibilityTrusted: Bool { AXIsProcessTrusted() }

    /// 弹出系统的“允许辅助功能”提示（只在没授权时有效果）
    static func requestAccessibility() {
        let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        _ = AXIsProcessTrustedWithOptions([key: true] as CFDictionary)
    }

    static func focusedWindowTitle(pid: pid_t) -> String? {
        guard AXIsProcessTrusted() else { return nil }
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 0.5) // 目标 App 卡住时不拖住我们
        var window: CFTypeRef?
        guard AXUIElementCopyAttributeValue(app, kAXFocusedWindowAttribute as CFString, &window) == .success,
              let window, CFGetTypeID(window) == AXUIElementGetTypeID()
        else { return nil }
        var title: CFTypeRef?
        guard AXUIElementCopyAttributeValue(window as! AXUIElement, kAXTitleAttribute as CFString, &title) == .success
        else { return nil }
        return title as? String
    }

    private static var electronAXEnabled: Set<pid_t> = []

    /// Electron App（Claude 桌面版）窗口里网页的标题，例如 "某会话 - Claude Code"。
    /// Electron 默认不建辅助功能树，第一次调用时打开（AXManualAccessibility），几秒后才读得到。
    static func webAreaTitle(pid: pid_t) -> String? {
        guard AXIsProcessTrusted() else { return nil }
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 0.5)
        if !electronAXEnabled.contains(pid) {
            AXUIElementSetAttributeValue(app, "AXManualAccessibility" as CFString, kCFBooleanTrue)
            electronAXEnabled.insert(pid)
        }
        var window: CFTypeRef?
        guard AXUIElementCopyAttributeValue(app, kAXFocusedWindowAttribute as CFString, &window) == .success,
              let window, CFGetTypeID(window) == AXUIElementGetTypeID()
        else { return nil }
        // 广度优先找第一个有标题的 AXWebArea，通常在第 20 个节点以内，设上限防止遍历整棵树
        var queue = [window as! AXUIElement]
        var visited = 0
        while !queue.isEmpty, visited < 80 {
            let element = queue.removeFirst()
            visited += 1
            var role: CFTypeRef?
            AXUIElementCopyAttributeValue(element, kAXRoleAttribute as CFString, &role)
            if role as? String == "AXWebArea" {
                var title: CFTypeRef?
                AXUIElementCopyAttributeValue(element, kAXTitleAttribute as CFString, &title)
                if let t = title as? String, !t.isEmpty { return t }
            }
            var children: CFTypeRef?
            AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &children)
            queue += (children as? [AXUIElement]) ?? []
        }
        return nil
    }

    // MARK: 自动化：Safari 当前标签页网址和标题

    private static let safariScript = NSAppleScript(source: """
    tell application id "com.apple.Safari"
        if (count of windows) is 0 then return {"", ""}
        set t to current tab of front window
        return {URL of t, name of t}
    end tell
    """)

    enum SafariResult {
        case page(url: String?, title: String?)
        case denied
    }

    static func safariPage() -> SafariResult {
        var error: NSDictionary?
        let result = safariScript?.executeAndReturnError(&error)
        if let error {
            // -1743：用户没有允许控制 Safari
            if (error[NSAppleScript.errorNumber] as? Int) == -1743 { return .denied }
            return .page(url: nil, title: nil)
        }
        let url = result?.atIndex(1)?.stringValue
        let title = result?.atIndex(2)?.stringValue
        return .page(url: url?.isEmpty == false ? url : nil, title: title?.isEmpty == false ? title : nil)
    }

    // MARK: 自动化：Safari 里 B站 / YouTube 视频的播放状态

    static var safariRunning: Bool {
        !NSRunningApplication.runningApplications(withBundleIdentifier: safariBundleId).isEmpty
    }

    // 在所有视频页标签页里执行一小段脚本，读 <video> 的播放 / 暂停、进度、时长、倍速。
    // 需要 Safari“开发”菜单里的“允许来自 Apple 事件的 JavaScript”；没开时 do JavaScript 报错，返回 "!"。
    // 页面卡住时最多等 2 秒，不拖住菜单栏。
    private static let videoScript = NSAppleScript(source: """
    set js to "(function(){var vs=[].slice.call(document.querySelectorAll('video'));if(!vs.length)return '';\
    var v=vs.filter(function(x){return !x.paused})[0]||vs.sort(function(a,b){return (b.duration||0)-(a.duration||0)})[0];\
    return [v.paused?0:1,v.currentTime,isFinite(v.duration)?v.duration:0,v.playbackRate].join(',');})()"
    tell application id "com.apple.Safari"
        set out to {}
        set wi to 0
        repeat with w in windows
            set wi to wi + 1
            try
                set ct to index of current tab of w
                set ts to tabs of w
            on error number n
                if n is -1743 then error number n
                set ts to {}
            end try
            repeat with t in ts
                set u to URL of t
                if u is not missing value and (u contains "bilibili.com/video/" or u contains "bilibili.com/bangumi/play/" \
                    or u contains "youtube.com/watch" or u contains "youtube.com/shorts/" or u contains "youtube.com/live/") then
                    try
                        with timeout of 2 seconds
                            set r to do JavaScript js in t
                        end timeout
                        if r is missing value then set r to ""
                    on error msg number n
                        if n is -1743 then error msg number n
                        set r to "!"
                    end try
                    set end of out to {u, name of t, r as text, (wi is 1 and index of t is ct)}
                end if
            end repeat
        end repeat
        return out
    end tell
    """)

    enum VideoResult {
        /// 没有打开 B站 / YouTube 视频页
        case none
        case tabs([VideoTab])
        /// 有视频页，但页面脚本执行不了（没开“允许来自 Apple 事件的 JavaScript”）
        case blocked
        case denied
    }

    static func safariVideos() -> VideoResult {
        // 只在 Safari 已经打开时执行：给没运行的 App 发 Apple 事件会把它启动起来
        guard safariRunning else { return .none }
        var error: NSDictionary?
        let result = videoScript?.executeAndReturnError(&error)
        if let error {
            if (error[NSAppleScript.errorNumber] as? Int) == -1743 { return .denied }
            NSLog("PresenceAgent: 读取 Safari 视频失败：\(error)")
            return .none
        }
        guard let list = result, list.numberOfItems > 0 else { return .none }
        var tabs: [VideoTab] = []
        var blocked = 0
        for i in 1 ... list.numberOfItems {
            guard let item = list.atIndex(i), item.numberOfItems >= 4,
                  let url = item.atIndex(1)?.stringValue, let probe = item.atIndex(3)?.stringValue
            else { continue }
            if probe == "!" {
                blocked += 1
                continue
            }
            tabs.append(VideoTab(url: url, title: item.atIndex(2)?.stringValue ?? "", probe: probe,
                                 front: item.atIndex(4)?.booleanValue ?? false))
        }
        return tabs.isEmpty && blocked > 0 ? .blocked : .tabs(tabs)
    }

    // MARK: 自动化：“音乐” App 当前曲目

    static let musicBundleId = "com.apple.Music"

    static var musicRunning: Bool {
        !NSRunningApplication.runningApplications(withBundleIdentifier: musicBundleId).isEmpty
    }

    // 只在“音乐”已经打开时执行：给没运行的 App 发 Apple 事件会把它启动起来
    private static let musicScript = NSAppleScript(source: """
    tell application id "com.apple.Music"
        try
            set ps to player state
            if ps is stopped then return {"stopped"}
            if ps is paused then
                set s to "paused"
            else
                set s to "playing"
            end if
            set t to current track
            return {s, persistent ID of t, name of t, artist of t, album of t, duration of t, player position}
        on error msg number n
            if n is -1743 then error msg number n
            return {"stopped"}
        end try
    end tell
    """)

    private static let artworkScript = NSAppleScript(source: """
    tell application id "com.apple.Music"
        try
            return raw data of artwork 1 of current track
        on error
            return ""
        end try
    end tell
    """)

    enum MusicResult {
        case notPlaying
        case track(MusicState)
        case denied
    }

    static func musicState() -> MusicResult {
        guard musicRunning else { return .notPlaying }
        var error: NSDictionary?
        let result = musicScript?.executeAndReturnError(&error)
        if let error {
            return (error[NSAppleScript.errorNumber] as? Int) == -1743 ? .denied : .notPlaying
        }
        guard let r = result, r.numberOfItems >= 7, let state = r.atIndex(1)?.stringValue,
              state != "stopped", let id = r.atIndex(2)?.stringValue, let title = r.atIndex(3)?.stringValue
        else { return .notPlaying }
        return .track(MusicState(
            trackId: id,
            title: title,
            artist: r.atIndex(4)?.stringValue ?? "",
            album: r.atIndex(5)?.stringValue ?? "",
            duration: r.atIndex(6)?.doubleValue ?? 0,
            position: r.atIndex(7)?.doubleValue ?? 0,
            playing: state == "playing"
        ))
    }

    /// 当前曲目的封面原始数据（JPEG / PNG 等）；没有封面时为 nil
    static func musicArtwork() -> Data? {
        guard musicRunning else { return nil }
        var error: NSDictionary?
        guard let result = artworkScript?.executeAndReturnError(&error), error == nil,
              result.descriptorType != typeUnicodeText, result.descriptorType != typeChar
        else { return nil }
        let data = result.data
        return data.isEmpty ? nil : data
    }

    // MARK: 空闲时间

    /// 距离上一次键盘 / 鼠标 / 触控板操作的秒数
    static func idleSeconds() -> Double {
        let service = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("IOHIDSystem"))
        guard service != 0 else { return 0 }
        defer { IOObjectRelease(service) }
        guard let value = IORegistryEntryCreateCFProperty(service, "HIDIdleTime" as CFString, kCFAllocatorDefault, 0)?
            .takeRetainedValue() as? NSNumber
        else { return 0 }
        return value.doubleValue / 1_000_000_000
    }
}
