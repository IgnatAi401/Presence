# 刷手机：42 条自动化，直接复用上报

已准备 42 个带原生 App 触发器的自动化模板，例如 `Presence · X · 打开`、`Presence · X · 退出`、`Presence · IG · 打开`。每个模板已包含“触发器 → 字典 → Presence · 上报”，只把触发器的具体 App 选择留空；上报字典里的 App 名、open/close 事件已经填好。全部直接复用现有上报，令牌仍集中保存在“上报”一处。

## 安装

1. **服务器先更新本次代码和 `server/config/rules.yaml`，然后重启后端**。本次新增了手机 App 文案，并让关闭事件立即结束手机状态。
2. 确认已更新支持 `phone_app` 的“Presence · 上报”并配置令牌。这版上报兼容已有睡觉、起床、吃饭和自定义状态。
3. 将 `automations/build/Presence-手机自动化42个.zip` 传到 iPhone，解压并导入其中的 42 个 `.shortcut` 文件。
4. 编辑每个模板，点击最上方触发器的 **App**，选择标题对应的软件。“打开／退出”已设定，下方字典和“运行快捷指令”无需重写。若系统要求确认运行方式，选择自动运行；根据需要在隐私设置允许锁定时运行。

模板使用 OS 27 的 `WFWorkflowTriggers` 格式，适用于支持将自动化触发器放进并共享快捷指令的新版系统。App 未选择前，系统会显示“配置 App 启用此自动化”，不会触发。旧版系统若不支持此格式，使用下方手工配置方式。模板不含令牌，不读取聊天、浏览记录、网页地址、商品、帖子或歌曲内容。

打开和退出的 App 选择都要各填一次；例如 `Presence · B站 · 打开` 和 `Presence · B站 · 退出` 都选择“哔哩哔哩”。只需选择触发器中的 App，字典的 `app: B站` 已经填写。

原生自动化的创建与编辑方式见 [Apple 使用说明](https://support.apple.com/en-qa/guide/shortcuts/apdfbdbd7123/ios)。

## 旧版系统的手工配置备选

以 B站为例：

1. iPhone“快捷指令”→“自动化”→“+”→“App”。
2. 选择哔哩哔哩，只勾选“已打开”，选择“立即运行”。
3. 新建空白自动化，添加“字典”动作，添加三个条目，类型都选“文本”：`kind` = `phone_app`、`app` = `B站`、`event` = `open`。
4. 在后面添加“运行快捷指令”，选择“Presence · 上报”。展开该动作，将“输入”设为前面“字典”的输出（系统若已自动连接，保留即可）。保存。
5. 再建一条 App 自动化，同样选哔哩哔哩，这次只勾选“已关闭”。动作仍是字典 → 运行“上报”，字典的 `event` 改为 `close`，其他字段相同。
6. 对其他 App 重复，只更换触发的 App 和字典的 `app`；打开和退出使用完全相同的 App 名称。共 42 条自动化，复用一个“上报”。不要把同一条自动化的“已打开”和“已关闭”同时勾上。

动作顺序示例：

```text
触发：哔哩哔哩 已打开
  字典：kind = phone_app、app = B站、event = open
  运行快捷指令：Presence · 上报
    输入：字典

触发：哔哩哔哩 已关闭
  字典：kind = phone_app、app = B站、event = close
  运行快捷指令：Presence · 上报
    输入：字典
```

这里的“已关闭”指离开该 App、返回桌面或切到别的 App，不需要在后台划掉应用。系统自动化的可用选项见 [Apple 说明](https://support.apple.com/guide/shortcuts/enable-or-disable-a-personal-automation-apd602971e63/ios)。如系统提供“运行时通知”，可关闭。

首次可在解锁状态下运行自动化的测试按钮，允许访问接口和调用“上报”；测试会改变公开状态。随后从桌面打开 App、再返回桌面，检查自动化是否真正触发。

## 公开文案

| 字典的 `app`（已预填） | iPhone 触发器中选择 | 打开后显示 |
| --- | --- | --- |
| X | X | 在 X 上刷动态 |
| Instagram | Instagram | 在 IG 看看大家的日常 |
| 小黑盒 | 小黑盒 | 在刷小黑盒 |
| 小红书 | 小红书 | 在小红书翻翻生活灵感 |
| 抖音 | 抖音 | 在抖音刷短视频 |
| TikTok | TikTok | 在 TikTok 刷短视频 |
| Reddit | Reddit（红迪） | 在 Reddit 逛社区 |
| Apple Music | 音乐 | 在 Apple Music 上听音乐 |
| Discord | Discord | 在 Discord 里聊天 |
| Telegram | Telegram | 在 Telegram 看消息、聊聊天 |
| WhatsApp | WhatsApp | 在 WhatsApp 和朋友聊聊天 |
| Facebook | Facebook | 在 Facebook 看看新动态 |
| Safari | Safari | 在用 Safari 逛网页 |
| B站 | 哔哩哔哩 | 在刷 B站 |
| YouTube | YouTube | 在 YouTube 看视频 |
| ChatGPT | ChatGPT | 在和 ChatGPT 聊天 |
| DeepSeek | DeepSeek | 在和 DeepSeek 聊天 |
| Claude | Claude | 在和 Claude 聊天 |
| 淘宝 | 淘宝 | 在淘宝淘点好东西 |
| 京东 | 京东 | 在京东逛逛 |
| 闲鱼 | 闲鱼 | 在闲鱼淘二手 |

文案集中在 `server/config/rules.yaml` 的 `phone_apps`，调整后重启后端即可，不必重做手机指令。文案按 App 设置，不在每次打开时随机变化，避免一次使用过程中状态反复改字。

## 行为和边界

- 打开 A 后打开 B，只显示 B；随后 A 的关闭事件晚到，也不会清掉 B。
- 关闭事件只停止同名 App 的手机状态，不清除吃饭、睡觉、办公室、自定义状态或其他设备的活动。重复关闭无副作用。
- 手机活动按 `phone` 分类、等级 4；有吃饭、办公室等锚定时，手机活动显示在伴随状态中。
- Apple Music 的文案依据“音乐 App 在前台”，不能证明歌曲正在播放；退出后只结束手机前台状态，音乐可能继续后台播放，也不影响 Mac 的实际歌曲卡片。
- 打开后有效期 30 分钟，防止系统漏发关闭事件后状态一直卡住。单次前台使用超过 30 分钟会到期，切出再进入会重新上报。未配置自动化的 App 无法自动识别。
- 锁屏、通话、系统权限和网络可能使自动化延迟或漏发；无法保证每次系统事件均被送达。断网不补发过时的打开/退出状态，恢复网络后重新进入 App。

## 请求协议

自动化直接传给共用上报的字典示例：

```json
{"kind":"phone_app","app":"B站","event":"open"}
```

上报根据输入 `kind` 选择 `phone_app` 和 1800 秒有效期；无此字段的原有手动状态仍按 `manual` 处理。请求是 JSON 文件正文，信号种类不再固定为 `manual`：

```json
{"kind":"phone_app","ttl":1800,"data":{"kind":"phone_app","app":"B站","event":"open"}}
```

`data.kind` 只是调用方的路由提示，后端忽略这个冗余字段。关闭时 `event` 为 `close`，App 名与打开时完全一致。

生成新版共用上报：

```sh
cd /Users/ignat/Developer/Presence/server
uv run --locked python ../shortcuts/build.py --reporter-only --sign
```

生成 42 个自动化模板（只在触发器中留空 App 选择）：

```sh
uv run --locked python ../shortcuts/build.py --automations --sign
```

模板 XML 在 `automations/source/`，签名文件在 `automations/build/`。触发器格式来自原生快捷指令应用实际导出的空 App 样例，记录在 `automations/trigger-format.json`；不读写快捷指令的内部数据库。

公开页根据接口中的 `app` 显示对应 App 图标，主状态和伴随状态均支持。21 个图标来自 App Store 官方素材，保存在网站本地静态目录；来源记录见 `web/public/icons/apps/sources.json`。此功能需要同步更新后端和网页。
