import AppKit
import SwiftUI

@main
struct PresenceAgentApp: App {
    @ObservedObject private var model = AgentModel.shared

    init() {
        AgentModel.shared.start()
    }

    var body: some Scene {
        MenuBarExtra {
            MenuContent(model: model)
        } label: {
            Image(nsImage: menuBarIcon(model.iconName))
        }
        .menuBarExtraStyle(.menu)
    }
}

/// 直接用 Image(systemName:) 时符号按默认小字号渲染，比系统图标小一圈；
/// 这里按菜单栏图标的常用尺寸和粗细生成模板图片（模板图片会自动跟随深浅色）
private func menuBarIcon(_ name: String) -> NSImage {
    let config = NSImage.SymbolConfiguration(pointSize: 16, weight: .medium)
    let image = NSImage(systemSymbolName: name, accessibilityDescription: "Presence")?
        .withSymbolConfiguration(config) ?? NSImage()
    image.isTemplate = true
    return image
}

struct MenuContent: View {
    @ObservedObject var model: AgentModel

    var body: some View {
        Text(model.statusLine)
        if let line = model.musicLine {
            Text(line)
        }
        if let line = model.videoLine {
            Text(line)
        }
        Text(model.reportLine)
        Divider()
        ForEach(Array(model.lastSignalLines.enumerated()), id: \.offset) { i, line in
            Text(i == 0 ? line : "    " + line)
        }
        if let json = model.lastSignalJSON {
            Button("复制这条上报的 JSON") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(json, forType: .string)
            }
        }
        if !model.accessibilityOK || model.safariDenied || model.videoBlocked || model.musicDenied {
            Divider()
        }
        if !model.accessibilityOK {
            Text("⚠️ 没有辅助功能权限，识别不了项目名")
        }
        if model.safariDenied {
            Text("⚠️ 没有控制 Safari 的权限，识别不了网站")
        } else if model.videoBlocked {
            Text("⚠️ Safari 没开“允许来自 Apple 事件的 JavaScript”，读不到视频进度")
            Button("怎么打开…") { SettingsWindowController.shared.show(model: model, tab: .permissions) }
        }
        if model.musicDenied {
            Text("⚠️ 没有控制“音乐”的权限，读不到正在听的歌")
        }
        Divider()
        if model.paused {
            Button("恢复上报") { model.resume() }
        } else {
            Button("暂停上报 1 小时") { model.pause() }
        }
        Button(model.configured ? "设置…" : "⚠️ 设置服务器和令牌…") {
            SettingsWindowController.shared.show(model: model)
        }
        .keyboardShortcut(",")
        Link("打开 now.ignat.ai", destination: URL(string: "https://now.ignat.ai")!)
        Divider()
        Button("退出 Presence") { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }
}
