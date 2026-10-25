import AppKit
import ApplicationServices
import IOKit

/// 读取系统信息：前台 App、窗口标题（辅助功能）、Safari 当前网址（自动化）、空闲时间（IOKit）。
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

    // MARK: 自动化：Safari 当前标签页网址

    private static let safariScript = NSAppleScript(source: """
    tell application id "com.apple.Safari"
        if (count of windows) is 0 then return ""
        return URL of current tab of front window
    end tell
    """)

    enum SafariResult {
        case url(String?)
        case denied
    }

    static func safariURL() -> SafariResult {
        var error: NSDictionary?
        let result = safariScript?.executeAndReturnError(&error)
        if let error {
            // -1743：用户没有允许控制 Safari
            if (error[NSAppleScript.errorNumber] as? Int) == -1743 { return .denied }
            return .url(nil)
        }
        return .url(result?.stringValue)
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
