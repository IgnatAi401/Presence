import AppKit
import SwiftUI

/// 设置窗口由 AppKit 直接管理。
/// 菜单栏小工具（LSUIElement）不是“前台 App”，SwiftUI 的 openWindow + NSApp.activate() 在 macOS 14 起
/// 改为“协商式激活”，系统常常拒绝激活，窗口就被压在其他 App 后面，看起来像点了没反应。
/// 这里打开窗口时临时切成普通 App（出现在程序坞）并强制置前，关窗后再切回纯菜单栏。
@MainActor
final class SettingsWindowController: NSObject, NSWindowDelegate {
    static let shared = SettingsWindowController()

    private var window: NSWindow?

    func show(model: AgentModel) {
        let window = self.window ?? makeWindow(model: model)
        self.window = window
        NSApp.setActivationPolicy(.regular)
        // 等菜单收起后再置前，否则菜单结束追踪时会把焦点抢回去
        DispatchQueue.main.async {
            NSApp.activate(ignoringOtherApps: true)
            window.makeKeyAndOrderFront(nil)
            window.orderFrontRegardless()
        }
    }

    private func makeWindow(model: AgentModel) -> NSWindow {
        // 分组样式的 Form 自带滚动，没有固定高度，交给窗口自动算尺寸会得到 0 高，内容显示不出来；
        // 所以由窗口给定尺寸，不让 SwiftUI 改窗口大小
        let hosting = NSHostingController(rootView: SettingsView(model: model))
        hosting.sizingOptions = []
        let window = NSWindow(contentViewController: hosting)
        window.setContentSize(NSSize(width: 460, height: 400))
        window.title = "Presence 设置"
        window.styleMask = [.titled, .closable]
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        return window
    }

    func windowWillClose(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
    }
}
