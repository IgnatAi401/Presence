# Presence 菜单栏小工具（Mac）

采集本机的前台信息上报给 presence-server：

| 采集 | 方式 | 需要的权限 |
| --- | --- | --- |
| 前台 App | `NSWorkspace` 切换通知 + 每 5 秒检查 | 无 |
| 项目名 | 白名单 App 的焦点窗口标题，只认 `~/Developer` 下已有的文件夹名，标题其他内容不上传 | 辅助功能 |
| Claude Code / Codex 工作区 | 标题里没有项目名时，看 `~/.claude/projects` 和 `~/.codex/sessions` 里最近 30 分钟内更新的会话，只取工作区对应的项目名，不读对话内容 | 无 |
| Safari 网站 | AppleScript 读当前标签页网址，只上报域名 | 自动化 → Safari |
| 空闲 / 锁屏 | IOKit `HIDIdleTime`、锁屏和休眠通知 | 无 |

状态变化时立即上报，没变化时每 2 分钟发一次心跳（有效期 5 分钟）。服务器连不上时信号存在
`~/Library/Application Support/PresenceAgent/queue.json`，恢复后按原时间补发。

## 构建与运行

```bash
cd agents/mac
swift test                        # 单元测试
scripts/build-app.sh --install    # 构建并安装到 ~/Applications/Presence.app，然后启动
```

第一次启动后：

1. 系统弹出“辅助功能”提示 → 打开系统设置，允许 **Presence**。
2. 切到 Safari 时会询问“Presence 想要控制 Safari” → 允许。
3. 菜单栏图标 → 设置…，填写接口地址和设备令牌（服务器 `settings.toml` 里 `[devices]` 的 `mac`）。

配置保存在 `~/Library/Application Support/PresenceAgent/config.json`（权限 600）。其中 `projectRoots`、`titleApps`、`agentApps`、`agentActiveSec`、
`heartbeatSec` 等可以手动修改，重启小工具生效。

**签名**：默认临时签名（ad-hoc），每次重新构建后 macOS 会把它当成新 App，需要在系统设置里重新勾选辅助功能
（先删掉旧的 Presence 再添加）。钥匙串里有开发证书时可以设置 `PRESENCE_SIGN_IDENTITY` 改用固定签名，避免这个问题。
