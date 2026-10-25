# Presence 菜单栏小工具（Mac）

采集本机的前台信息上报给 presence-server：

| 采集 | 方式 | 需要的权限 |
| --- | --- | --- |
| 前台 App | `NSWorkspace` 切换通知 + 每 5 秒检查 | 无 |
| 项目名 | 白名单 App 的焦点窗口标题，只认 `~/Developer` 下已有的文件夹名，标题其他内容不上传 | 辅助功能 |
| Claude Code / Codex 工作区 | 标题里没有项目名时，看 `~/.claude/projects` 和 `~/.codex/sessions` 里最近 30 分钟内更新的会话，只取工作区对应的项目名，不读对话内容 | 无 |
| Safari 网页 | AppleScript 读当前标签页的网址和标题，上报域名和标题（不上报完整网址）。设置里“不公开的网站”中的域名（含子域名）只上报“在上网”，域名、标题、视频都不上报 | 自动化 → Safari |
| B站 / YouTube 视频 | Safari 已打开时，对视频页标签页执行一小段页面脚本，读播放 / 暂停、进度、时长、倍速，以及视频是否滚出画面（在看评论）；BV 号、分 P、视频 ID 从网址里取。看视频时空闲再久也照常读 | 自动化 → Safari，以及 Safari 的“允许来自 Apple 事件的 JavaScript” |
| Apple Music | “音乐”已打开时用 AppleScript 读当前曲目（歌名、歌手、专辑、时长、进度、播放 / 暂停）和封面；“音乐”没打开时不会去启动它 | 自动化 → 音乐 |
| 空闲 / 锁屏 | IOKit `HIDIdleTime`、锁屏和休眠通知 | 无 |

状态变化时立即上报，没变化时每 2 分钟发一次心跳（有效期 5 分钟）。音乐在换歌、暂停 / 播放、拖动进度时立即上报，
播放中每 2 分钟刷新一次；封面先缩到最长边 600 像素以内，按 sha256 查服务器有没有，没有才上传（`PUT media/{sha256}`），
传好后信号里带上 `cover_hash`。视频用同一套规则，推算进度时考虑倍速。服务器连不上时信号存在
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
3. 想上报视频进度：Safari → 设置 → 高级 → 勾选“在菜单栏中显示开发菜单”；然后菜单栏“开发” → 勾选“允许来自 Apple 事件的 JavaScript”。
   没开时，打开 B站 / YouTube 视频页后菜单里会出现警告。注意：开了以后，被允许控制 Safari 的程序都能在网页里执行脚本，只给 Presence 授权即可。
4. 打开“音乐”时会询问“Presence 想要控制‘音乐’” → 允许。不小心点了不允许：系统设置 → 隐私与安全性 → 自动化 → Presence，打开“音乐”。
5. 菜单栏图标 → 设置…，填写接口地址和设备令牌（服务器 `settings.toml` 里 `[devices]` 的 `mac`）。

配置保存在 `~/Library/Application Support/PresenceAgent/config.json`（权限 600）。其中 `projectRoots`、`titleApps`、`agentApps`、`agentActiveSec`、
`heartbeatSec` 等可以手动修改，重启小工具生效。

**签名**：默认临时签名（ad-hoc），每次重新构建后 macOS 会把它当成新 App，需要在系统设置里重新勾选辅助功能
（先删掉旧的 Presence 再添加）。钥匙串里有开发证书时可以设置 `PRESENCE_SIGN_IDENTITY` 改用固定签名，避免这个问题。
