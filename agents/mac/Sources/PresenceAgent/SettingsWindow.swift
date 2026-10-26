import AppKit
import PresenceAgentCore
import SwiftUI

/// 设置窗口由 AppKit 直接管理。
/// 菜单栏小工具（LSUIElement）不是“前台 App”，SwiftUI 的 openWindow + NSApp.activate() 在 macOS 14 起
/// 改为“协商式激活”，系统常常拒绝激活，窗口就被压在其他 App 后面，看起来像点了没反应。
/// 这里打开窗口时临时切成普通 App（出现在程序坞）并强制置前，关窗后再切回纯菜单栏。
@MainActor
final class SettingsWindowController: NSObject, NSWindowDelegate {
    static let shared = SettingsWindowController()

    enum Tab: Int, CaseIterable {
        case general, projects, privacy, permissions
    }

    private var window: NSWindow?
    private var tabs: NSTabViewController?

    func show(model: AgentModel, tab: Tab? = nil) {
        let window = self.window ?? makeWindow(model: model)
        self.window = window
        if let tab { select(tab) }
        NSApp.setActivationPolicy(.regular)
        // 等菜单收起后再置前，否则菜单结束追踪时会把焦点抢回去
        DispatchQueue.main.async {
            NSApp.activate(ignoringOtherApps: true)
            window.makeKeyAndOrderFront(nil)
            window.orderFrontRegardless()
        }
    }

    func select(_ tab: Tab) {
        tabs?.selectedTabViewItemIndex = tab.rawValue
    }

    /// 系统设置风格的工具栏标签页：通用 / 项目 / 隐私 / 权限（顺序和 Tab 一致）
    private func makeWindow(model: AgentModel) -> NSWindow {
        let tabs = NSTabViewController()
        tabs.tabStyle = .toolbar
        tabs.addTabViewItem(page("通用", "gearshape", GeneralSettings(model: model)))
        tabs.addTabViewItem(page("项目", "folder", ProjectSettings(model: model)))
        tabs.addTabViewItem(page("隐私", "hand.raised", PrivacySettings(model: model)))
        tabs.addTabViewItem(page("权限", "lock.shield", PermissionSettings(model: model)))
        self.tabs = tabs

        let window = NSWindow(contentViewController: tabs)
        window.styleMask = [.titled, .closable]
        window.toolbarStyle = .preference
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        return window
    }

    /// 分组样式的 Form 自带滚动，没有固定高度，交给窗口自动算尺寸会得到 0 高，内容显示不出来；
    /// 所以每页给定同样的尺寸，不让 SwiftUI 改窗口大小，切换标签页时窗口也不跳动
    private func page(_ label: String, _ symbol: String, _ view: some View) -> NSTabViewItem {
        let size = NSSize(width: 500, height: 560)
        let hosting = NSHostingController(rootView: view.frame(width: size.width, height: size.height))
        hosting.sizingOptions = []
        hosting.preferredContentSize = size
        hosting.view.frame.size = size
        hosting.title = label // 工具栏标签页样式下，窗口标题跟随当前页
        let item = NSTabViewItem(viewController: hosting)
        item.label = label
        item.image = NSImage(systemSymbolName: symbol, accessibilityDescription: label)
        return item
    }

    func windowWillClose(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
    }
}

/// 分组下方的说明文字；macOS 分组表单默认靠右排，多行时难读，这里改成靠左
private struct FooterText: View {
    let text: String
    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text)
            .font(.caption).foregroundStyle(.secondary)
            .multilineTextAlignment(.leading)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - 通用

private struct GeneralSettings: View {
    @ObservedObject var model: AgentModel
    @State private var server = ""
    @State private var token = ""
    @State private var revealToken = false
    @State private var message: String?

    private var dirty: Bool {
        server.trimmingCharacters(in: .whitespacesAndNewlines) != model.config.server
            || token.trimmingCharacters(in: .whitespacesAndNewlines) != model.config.token
    }

    var body: some View {
        Form {
            Section {
                StatusRow(model: model)
                LabeledContent("此刻") {
                    Text(model.statusLine).lineLimit(1).truncationMode(.middle)
                }
                if model.missingPermissions > 0 {
                    HStack {
                        Label("\(model.missingPermissions) 项权限未开启，部分信息采集不到", systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                        Spacer()
                        Button("查看") { SettingsWindowController.shared.select(.permissions) }
                    }
                }
            }

            Section {
                TextField("接口地址", text: $server, prompt: Text("https://api.ignat.ai/presence/api"))
                LabeledContent("设备令牌") {
                    HStack(spacing: 6) {
                        Group {
                            if revealToken {
                                TextField("设备令牌", text: $token, prompt: Text("必填"))
                            } else {
                                SecureField("设备令牌", text: $token, prompt: Text("必填"))
                            }
                        }
                        .labelsHidden()
                        .multilineTextAlignment(.trailing)
                        Button {
                            revealToken.toggle()
                        } label: {
                            Image(systemName: revealToken ? "eye.slash" : "eye")
                        }
                        .buttonStyle(.borderless)
                        .help(revealToken ? "隐藏令牌" : "显示令牌")
                    }
                }
                HStack {
                    if let message {
                        Text(message).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("还原") { load() }
                        .disabled(!dirty)
                    Button("保存") { save() }
                        .keyboardShortcut(.defaultAction)
                        .disabled(!dirty)
                }
            } header: {
                Text("服务器")
            } footer: {
                FooterText("设备令牌是服务器 settings.toml 里 [devices] 下的 mac 那一行。")
            }

            Section {
                HStack {
                    Text("扫描目录、识别哪些 App、心跳间隔等在配置文件里修改，重启小工具后生效。")
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("显示配置文件") {
                        NSWorkspace.shared.activateFileViewerSelecting(
                            [ConfigStore.directory.appendingPathComponent("config.json")])
                    }
                }
            } header: {
                Text("高级")
            }
        }
        .formStyle(.grouped)
        .onAppear(perform: load)
        .onChange(of: server) { message = nil }
        .onChange(of: token) { message = nil }
    }

    private func load() {
        server = model.config.server
        token = model.config.token
        message = nil
    }

    private func save() {
        do {
            try model.saveConnection(server: server, token: token)
            load()
            message = "已保存，正在重新连接…"
        } catch {
            message = "保存失败：\(error.localizedDescription)"
        }
    }
}

/// 顶部的上报状态：图标 + 一句结论 + 详情，右侧是暂停 / 恢复
private struct StatusRow: View {
    @ObservedObject var model: AgentModel

    private var summary: (title: String, tint: Color) {
        if model.paused { return ("已暂停上报", .secondary) }
        switch model.outcome {
        case .sent: return ("正常上报中", .green)
        case .idle: return ("等待第一次上报", .secondary)
        case .failed: return ("连不上服务器", .orange)
        case .unauthorized: return ("令牌无效", .red)
        case .notConfigured: return ("还没填写服务器和令牌", .orange)
        }
    }

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: model.iconName)
                .font(.title2)
                .foregroundStyle(summary.tint)
                .frame(width: 30)
            VStack(alignment: .leading, spacing: 2) {
                Text(summary.title).font(.headline)
                Text(model.reportLine).font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            if model.paused {
                Button("恢复上报") { model.resume() }
            } else {
                Button("暂停 1 小时") { model.pause() }
            }
        }
        .padding(.vertical, 4)
    }
}

// MARK: - 项目

/// 每个项目文件夹算科研还是开发；选“按 App”时交给服务器按前台 App 判断
private struct ProjectSettings: View {
    @ObservedObject var model: AgentModel
    @State private var error: String?

    var body: some View {
        Form {
            Section {
                if model.projectNames.isEmpty {
                    Text("在 \(model.config.projectRoots.joined(separator: "、")) 下没有找到文件夹")
                        .foregroundStyle(.secondary)
                }
                ForEach(model.projectNames, id: \.self) { name in
                    Picker(selection: category(name)) {
                        Text("按 App").tag("")
                        Text("科研").tag("research")
                        Text("开发").tag("dev")
                    } label: {
                        Label(name, systemImage: "folder").lineLimit(1).truncationMode(.middle)
                    }
                    .pickerStyle(.segmented)
                }
            } header: {
                HStack {
                    Text("项目分类")
                    Spacer()
                    Text("\(model.config.projectRoots.joined(separator: "、")) · \(model.projectNames.count) 个")
                        .font(.callout).fontWeight(.regular).foregroundStyle(.secondary)
                    Button {
                        model.rescanProjects()
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .buttonStyle(.borderless)
                    .help("重新扫描项目文件夹")
                }
            } footer: {
                VStack(alignment: .leading, spacing: 4) {
                    if let error {
                        Text(error).font(.caption).foregroundStyle(.red)
                    }
                    FooterText("在 VS Code、终端、Claude Code、Codex 里打开项目时，按这里的分类显示“搞科研”或“搞开发”，改动立即生效。选“按 App”时由服务器判断：VS Code、Claude、Codex 等算开发，MATLAB、PowerPoint 等算科研。新建文件夹后点右上角的刷新。")
                }
            }
        }
        .formStyle(.grouped)
    }

    private func category(_ project: String) -> Binding<String> {
        Binding {
            model.config.projectCategories[project] ?? ""
        } set: { value in
            do {
                try model.setProjectCategory(project, value.isEmpty ? nil : value)
                error = nil
            } catch {
                self.error = "保存失败：\(error.localizedDescription)"
            }
        }
    }
}

// MARK: - 隐私

private struct PrivacySettings: View {
    @ObservedObject var model: AgentModel
    @State private var draft = ""
    @State private var error: String?

    private var parsed: [String] { AgentConfig.parseDomains(draft) }

    var body: some View {
        Form {
            Section {
                if model.config.hiddenDomains.isEmpty {
                    Text("还没有添加网站").foregroundStyle(.secondary)
                }
                ForEach(model.config.hiddenDomains, id: \.self) { domain in
                    HStack {
                        Image(systemName: "globe").foregroundStyle(.secondary)
                        Text(domain)
                        Spacer()
                        Button {
                            update(model.config.hiddenDomains.filter { $0 != domain })
                        } label: {
                            Image(systemName: "minus.circle.fill")
                        }
                        .buttonStyle(.borderless)
                        .foregroundStyle(.secondary)
                        .help("移除 \(domain)")
                    }
                }
                HStack {
                    TextField("添加网站", text: $draft, prompt: Text("输入域名或粘贴网址，例如 zhihu.com"))
                        .labelsHidden()
                        .onSubmit(add)
                    Button("添加", action: add)
                        .disabled(parsed.isEmpty)
                }
            } header: {
                Text("不公开的网站")
            } footer: {
                VStack(alignment: .leading, spacing: 4) {
                    if let error {
                        Text(error).font(.caption).foregroundStyle(.red)
                    }
                    FooterText("这些网站（含子域名）的域名、标题和视频照常私有保存到服务器，供以后自己回看；公开网页上只显示“网上冲浪”。改动立即生效。")
                }
            }
        }
        .formStyle(.grouped)
    }

    private func add() {
        let new = parsed.filter { !model.config.hiddenDomains.contains($0) }
        guard !parsed.isEmpty else { return }
        update(model.config.hiddenDomains + new)
        if error == nil { draft = "" }
    }

    private func update(_ domains: [String]) {
        do {
            try model.setHiddenDomains(domains)
            error = nil
        } catch {
            self.error = "保存失败：\(error.localizedDescription)"
        }
    }
}

// MARK: - 权限

private struct PermissionSettings: View {
    @ObservedObject var model: AgentModel

    private static let accessibilityURL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
    private static let automationURL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"

    var body: some View {
        Form {
            Section {
                PermissionRow(
                    symbol: "accessibility", color: .blue,
                    title: "辅助功能", detail: "从窗口标题识别项目名",
                    ok: model.accessibilityOK, settingsURL: Self.accessibilityURL
                )
                PermissionRow(
                    symbol: "safari", color: .cyan,
                    title: "控制 Safari", detail: "读取当前网站的域名和标题",
                    ok: !model.safariDenied, settingsURL: Self.automationURL
                )
                PermissionRow(
                    symbol: "play.rectangle.fill", color: .pink,
                    title: "Safari 网页脚本", detail: "读取 B站 / YouTube 的视频进度",
                    ok: !model.videoBlocked,
                    steps: "Safari → 设置 → 高级 → 勾选“在菜单栏中显示开发菜单”；然后菜单栏“开发” → 勾选“允许来自 Apple 事件的 JavaScript”。"
                )
                PermissionRow(
                    symbol: "music.note", color: .red,
                    title: "控制“音乐”", detail: "读取正在听的歌和封面",
                    ok: !model.musicDenied, settingsURL: Self.automationURL
                )
            } footer: {
                FooterText("Safari 和“音乐”的权限要等第一次用到时才能检测；授权后几秒内这里会自动更新。")
            }
        }
        .formStyle(.grouped)
    }
}

private struct PermissionRow: View {
    let symbol: String
    let color: Color
    let title: String
    let detail: String
    let ok: Bool
    var settingsURL: String?
    /// 不在系统设置里的开关（例如 Safari 的选项），没打开时显示操作步骤
    var steps: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Image(systemName: symbol)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(width: 24, height: 24)
                    .background(color.gradient, in: RoundedRectangle(cornerRadius: 6))
                VStack(alignment: .leading, spacing: 1) {
                    Text(title)
                    Text(detail).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if ok {
                    Label("已允许", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                        .font(.callout)
                } else if let settingsURL {
                    Button("打开系统设置") {
                        if let url = URL(string: settingsURL) { NSWorkspace.shared.open(url) }
                    }
                } else {
                    Label("未开启", systemImage: "exclamationmark.circle.fill")
                        .foregroundStyle(.orange)
                        .font(.callout)
                }
            }
            if !ok, let steps {
                Text(steps)
                    .font(.caption).foregroundStyle(.secondary)
                    .padding(.leading, 34)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
