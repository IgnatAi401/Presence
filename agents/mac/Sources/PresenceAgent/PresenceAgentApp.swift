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
            Button("怎么打开…") { SettingsWindowController.shared.show(model: model) }
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

struct SettingsView: View {
    @ObservedObject var model: AgentModel
    @State private var server = ""
    @State private var token = ""
    @State private var hiddenDomains = ""
    @State private var message = ""

    var body: some View {
        Form {
            Section("上报") {
                TextField("接口地址", text: $server, prompt: Text("https://api.ignat.ai/presence/api"))
                SecureField("设备令牌", text: $token)
                HStack {
                    Text(message).foregroundStyle(.secondary)
                    Spacer()
                    Button("保存") {
                        do {
                            try model.save(server: server, token: token, hiddenDomains: hiddenDomains)
                            hiddenDomains = model.config.hiddenDomains.joined(separator: "\n")
                            message = "已保存"
                        } catch {
                            message = "保存失败：\(error.localizedDescription)"
                        }
                    }
                    .keyboardShortcut(.defaultAction)
                }
                Text(model.reportLine).font(.caption).foregroundStyle(.secondary)
            }
            Section("不公开的网站") {
                TextField("每行一个域名，例如 zhihu.com", text: $hiddenDomains, axis: .vertical)
                    .lineLimit(3 ... 8)
                Text("这些网站（含子域名）的域名、标题和视频会私有保存到服务器，公开网页只显示“网上冲浪”，供以后自己回看。改完点上面的“保存”。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("权限") {
                PermissionRow(
                    title: "辅助功能（读取窗口标题里的项目名）",
                    ok: model.accessibilityOK,
                    url: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
                )
                PermissionRow(
                    title: "自动化 → Safari（读取当前网站的域名）",
                    ok: !model.safariDenied,
                    url: "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"
                )
                PermissionRow(
                    title: "Safari 允许来自 Apple 事件的 JavaScript（读取视频进度）",
                    ok: !model.videoBlocked,
                    hint: "Safari → 设置 → 高级 → 勾选“在菜单栏中显示开发菜单”；然后菜单栏“开发” → 勾选“允许来自 Apple 事件的 JavaScript”。"
                )
                PermissionRow(
                    title: "自动化 → 音乐（读取正在听的歌和封面）",
                    ok: !model.musicDenied,
                    url: "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"
                )
                Text("已找到 \(model.projectCount) 个项目文件夹（\(model.config.projectRoots.joined(separator: "、"))）")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .frame(minWidth: 460, minHeight: 560)
        .onAppear {
            server = model.config.server
            token = model.config.token
            hiddenDomains = model.config.hiddenDomains.joined(separator: "\n")
        }
    }
}

struct PermissionRow: View {
    let title: String
    let ok: Bool
    var url: String?
    /// 不在系统设置里的开关（例如 Safari 的选项），没打开时显示操作步骤
    var hint: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Image(systemName: ok ? "checkmark.circle.fill" : "exclamationmark.circle")
                    .foregroundStyle(ok ? .green : .orange)
                Text(title)
                Spacer()
                if !ok, let url {
                    Button("打开系统设置") {
                        if let u = URL(string: url) { NSWorkspace.shared.open(u) }
                    }
                }
            }
            if !ok, let hint {
                Text(hint).font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}
