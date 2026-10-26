# 上报信号

所有设备都往同一个接口发：`POST /presence/api/signal`，请求头带设备令牌，内容是 `{"signals": [...]}`。
每条信号是 `{kind, ts, ttl, data}`：`ts` 省略时按服务器时间，`ttl` 是这条信号多久内有效（秒）。

分工只有一条：**设备只报看到了什么，分类、文案、名字和图标都由服务器按 `server/config/rules.yaml` 决定。**
信号进服务器后先经过 `server/presence/signals.py` 翻译成统一写法，再交给状态引擎。

## 上报类型

| kind | 谁发 | 归到哪个类别 | data 里的主要字段 |
| --- | --- | --- | --- |
| `desktop` | Mac 小工具 | 按 `rules.yaml` 分类：项目 → 域名 → App 默认值 | `bundle_id` `app` `idle_sec` `locked` `project` `category` `domain` `title` `private` |
| `music` | Mac 小工具、AIMP 插件 | `music` | `player` `title` `artist` `album` `state` `position` `duration` `cover_hash` |
| `video` | Mac 小工具 | `video` | `site` `id` `page` `state` `position` `duration` `rate` `viewing` `private` |
| `game` | 服务器自己查 Steam | `game` | `name` `appid` `event`（`start` / `stop`） |
| `phone_app` | iPhone 快捷指令 | `phone` | `app`（`rules.yaml` 里 `phone_apps` 的键） `event`（`open` / `close`） |
| `manual` | iPhone 快捷指令 | 由 `state` 决定，见下 | `state` `action` `minutes` `text` `tier` |
| `heartbeat` | 任何设备 | 不分类，只给这台设备续期 | 无 |

`desktop` 的 `category` 是小工具设置里给项目选的类别，只认 `research` 和 `dev`。没选的项目按 App 默认值算。

## 手动状态（manual）

统一写法是 `{"state": 状态, "action": "start" | "stop"}`，`action` 省略时视为 `start`。

| state | 含义 |
| --- | --- |
| `sleep` `eat` `office` `out` `custom` | 开始 / 结束对应状态 |
| `class` | 只能 `stop`：取消正在上的课 |
| 省略 state、`action: "stop"` | 结束全部手动状态 |

已经装在手机上的快捷指令用的是旧写法，服务器会照常识别，快捷指令不用改：

| 旧写法 | 翻译成 |
| --- | --- |
| `wake` | `sleep` + `stop` |
| `stop_eat` | `eat` + `stop` |
| `leave_office` | `office` + `stop` |
| `stop_out` | `out` + `stop` |
| `stop_custom` | `custom` + `stop` |
| `cancel_class` | `class` + `stop` |
| `clear` | 结束全部 |

私有标记统一用 `private`。旧版 Mac 小工具发的 `hidden` 会被翻译成 `private`。

## 显示用的名字和图标

网页不再自带对照表，都从服务器返回的字段里取：

| 字段 | 来源（`rules.yaml`） |
| --- | --- |
| 手机状态的 `icon`、文案 | `phone_apps` |
| 视频卡片的 `card.site_name`、Safari 看视频时的“看 B站” | `domains` 里写了 `name` / `video` 的条目 |
| 音乐卡片的 `card.player_name` | `players` |
| 合并进锚定状态的活动简称 `activity_name`，`/today` 里的 `names` | `anchors` / `activities` 的 `short` |

新增一个手机 App：在 `phone_apps` 里加一行，把 128×128 的图标放进 `web/public/icons/apps/`，并在同目录的 `sources.json` 里记下来源。
