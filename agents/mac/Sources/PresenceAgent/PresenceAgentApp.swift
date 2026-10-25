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
        if !model.accessibilityOK || model.safariDenied {
            Divider()
        }
        if !model.accessibilityOK {
            Text("⚠️ 没有辅助功能权限，识别不了项目名")
        }
        if model.safariDenied {
            Text("⚠️ 没有控制 Safari 的权限，识别不了网站")
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
                            try model.save(server: server, token: token)
                            message = "已保存"
                        } catch {
                            message = "保存失败：\(error.localizedDescription)"
                        }
                    }
                    .keyboardShortcut(.defaultAction)
                }
                Text(model.reportLine).font(.caption).foregroundStyle(.secondary)
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
                Text("已找到 \(model.projectCount) 个项目文件夹（\(model.config.projectRoots.joined(separator: "、"))）")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .frame(minWidth: 460, minHeight: 400)
        .onAppear {
            server = model.config.server
            token = model.config.token
        }
    }
}

struct PermissionRow: View {
    let title: String
    let ok: Bool
    let url: String

    var body: some View {
        HStack {
            Image(systemName: ok ? "checkmark.circle.fill" : "exclamationmark.circle")
                .foregroundStyle(ok ? .green : .orange)
            Text(title)
            Spacer()
            if !ok {
                Button("打开系统设置") {
                    if let u = URL(string: url) { NSWorkspace.shared.open(u) }
                }
            }
        }
    }
}
