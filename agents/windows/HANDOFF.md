# 交接：AIMP 插件上报正在听的歌（Windows）

> 写给在 Windows 上开发 AIMP 插件的 agent。本文自成一体：你拿不到服务器代码也能照着做。
> 更新于 2026-10-25。如有和服务器实际行为不一致的地方，以服务器返回为准，并告诉用户。

## 1. 背景

Presence 是一个“我此刻在做什么”的实时状态系统，网页是 now.ignat.ai。组成：

| 部分 | 位置 | 状态 |
| --- | --- | --- |
| 服务器（FastAPI + SQLite） | 一台 Mac，`127.0.0.1:8790` | 已完成，支持音乐卡片、封面 |
| Mac 菜单栏小工具 | Mac | 已完成，含 Apple Music 上报（本文的参考实现） |
| 网页 | now.ignat.ai | 已能显示音乐卡片，**尚未上线** |
| **AIMP 插件** | Windows | **本次任务** |

用户主要在 Windows 上用 **AIMP** 听歌。插件要做的事：AIMP 播放时把歌名、歌手、专辑、进度、播放 / 暂停状态和封面上报给服务器，网页上就会出现一张音乐卡片（封面、歌名、进度条、“已播放 / 总时长”、右上角“连续听歌 X 分钟”）。

**不需要做**：前台窗口检测、游戏检测（以后可能另做）、网页、服务器。

## 2. 交付要求

1. AIMP 原生插件（AIMP SDK，语言自选；C++ 或 C# 均可），用户把它放进 AIMP 的 `Plugins` 目录、在 AIMP 设置里启用即可工作。
2. 配置项：**接口地址**和**设备令牌**，存在用户目录（例如 `%APPDATA%\PresenceAIMP\config.json`），不要写进代码或 Git。最好在 AIMP 的插件设置页里能填写；做不到的话，读配置文件也可以，但要在 README 里写清楚。
3. 网络请求一律在后台线程做，**不能卡住 AIMP 的界面和播放**。
4. 服务器连不上时信号先存本地队列（持久化到磁盘），恢复后按原时间补发。
5. 附一份给初学者看的中文 README：安装、配置、怎么确认在工作、怎么卸载。

## 3. 连接服务器

| 场景 | 接口地址（下文称 `{API}`） |
| --- | --- |
| 正式 | `https://api.ignat.ai/presence/api` |
| 在服务器 Mac 本机调试 | `http://127.0.0.1:8790/presence/api` |

> ⚠️ **现在 Windows 还连不上服务器。** 服务器只监听 Mac 的 `127.0.0.1`，要等用户配置好 Cloudflare Tunnel
> （`api.ignat.ai/presence/api/*` → Mac 的 8790 端口）后，正式地址才能用。开发阶段请先用第 9 节的**模拟服务器**测试；
> 端到端联调放到 Tunnel 配好之后。

### 鉴权

上报类接口带请求头：

```
Authorization: Bearer <设备令牌>
```

- 令牌由用户在服务器的 `server/config/settings.toml` 里生成，Windows 对应 `[devices]` 下的 `windows = "…"` 一行。
  服务器按令牌判断信号来源（`source = "windows"`），客户端不需要、也不能自报来源。
- 令牌是秘密：不要打印到日志，不要出现在网址里。

### 频率限制

每台设备**每分钟最多 60 次**上报类请求（`POST signal` 和 `PUT media` 合计），超过返回 `429`。按第 6 节的节奏发远远用不完。

## 4. 接口

### 4.1 上报信号 `POST {API}/signal`

```
POST {API}/signal
Authorization: Bearer <设备令牌>
Content-Type: application/json
```

请求体（批量，一次最多 500 条；也可以直接发单条对象，不包 `signals`）：

```json
{
  "signals": [
    {
      "kind": "music",
      "ts": 1792904523.7,
      "ttl": 300,
      "data": {
        "player": "aimp",
        "title": "晴天",
        "artist": "周杰伦",
        "album": "叶惠美",
        "state": "playing",
        "position": 83.2,
        "duration": 269.0,
        "cover_hash": "3ba13e5e5542b71dbfdc5edddb6fb731c73898e10fc2a206e6f3cb7dd2345efd"
      }
    }
  ]
}
```

| 字段 | 说明 |
| --- | --- |
| `kind` | 固定 `"music"` |
| `ts` | 这条状态发生的时间，Unix 秒（可带小数）。补发离线队列时保留原值。比服务器时间还晚的会被按服务器时间处理，所以 Windows 要开自动对时 |
| `ttl` | 有效期（秒），`0 < ttl ≤ 86400`。播放中用 `300` |
| `data.player` | 固定 `"aimp"` |
| `data.title` / `artist` / `album` | 字符串；没有就传 `""`。没有标题时用文件名（去掉扩展名） |
| `data.state` | `"playing"` 或 `"paused"`。**停止、关闭 AIMP 也发 `"paused"`**（网页只认这两个值） |
| `data.position` / `duration` | 秒，数字。网络电台等没有时长的传 `0`（网页就不画进度条） |
| `data.cover_hash` | 可选。封面图片的 sha256，**只有服务器确认已有这张图后才带上**（见 4.2） |

返回：

| 状态码 | 含义 | 客户端怎么做 |
| --- | --- | --- |
| `200` `{"ok": true, "accepted": 1}` | 收到 | 从队列删掉 |
| `401` | 令牌无效 | 保留队列，停止重试，提示用户检查令牌 |
| `400` / `422` | 数据格式不对 | 丢掉这批（重发也没用），记日志 |
| `429` / `5xx` / 连不上 | 暂时失败 | 保留队列，退避重试（5 秒起，翻倍，最长 5 分钟） |

### 4.2 封面

封面图片按**内容的 sha256** 存放，同一张图只传一次。

**查询有没有**（公开接口，不用令牌）：

```
HEAD {API}/media/{sha256}      → 200 已有 / 404 没有
```

**上传**：

```
PUT {API}/media/{sha256}
Authorization: Bearer <设备令牌>
Content-Type: image/jpeg            （服务器按文件头判断格式，这个头只是礼貌）
<图片原始字节>
```

| 要求 | 说明 |
| --- | --- |
| `{sha256}` | 上传字节的 sha256，**64 位小写十六进制**；服务器会重算，不一致返回 `400` |
| 格式 | 只收 JPEG / PNG / WebP（按文件头判断），否则 `415` |
| 大小 | 不超过 2 MB（2097152 字节），否则 `413` |
| 返回 | `201 {"ok":true,"created":true}` 新存；`200 {"ok":true,"created":false}` 本来就有；`401` 令牌无效 |

**建议做法**（和 Mac 小工具一致）：

1. 从 AIMP 取当前曲目的封面（内嵌封面或同目录的 cover 图，AIMP 自己就会找）。
2. 如果本来就是 JPEG / PNG 且不超过 400 KB，原样使用；否则缩到**最长边 600 像素**、转成 JPEG（质量约 85）。
3. 对**最终要上传的字节**算 sha256。
4. 内存里记一份“已确认上传的哈希”集合；不在集合里时：先 `HEAD`，404 再 `PUT`；成功后加入集合。失败的话 30 秒后再试，不要每次采样都重传。
5. 换歌时先**不带** `cover_hash` 立即上报（让网页马上更新歌名），封面确认后再补发一条带 `cover_hash` 的。
6. 按曲目缓存处理结果（同一首歌不要反复缩图、算哈希）。没有封面就一直不带 `cover_hash`。

封面读取地址是 `GET {API}/media/{sha256}`（带一年的缓存头），网页会自己拼，插件不用管。

## 5. 服务器怎么理解音乐信号

插件只要按第 6 节发信号，下面这些服务器都已经做好，**不要在插件里重复实现**：

- **一首歌的身份是 `(title, artist)`**。这两个值变了就算换歌，开新的统计片段；专辑、进度变化不算。
- **只有 `state == "playing"` 算在听**。收到 `paused` 后卡片显示“已暂停”，**保留 1 分钟后消失**；1 分钟内恢复播放接着算。
- **超过 `ttl` 没收到新信号**，服务器认为已经停了，卡片直接消失（不保留 1 分钟）。所以播放中必须按心跳刷新。
- **连续听歌时长**：换歌、1 分钟内的暂停都接着算；停超过 1 分钟后重新开始计时。
- **多台设备同时放歌**（Mac 的 Apple Music 和 Windows 的 AIMP）：各显示一张卡片，卡片上按 `player` 显示播放器图标，所以 `player` 必须填 `aimp`。
- **进度条**：网页用 `position + (现在 − 信号时间)` 自己推算，所以**不需要每秒上报**。

## 6. 什么时候发

| 事件 | 动作 |
| --- | --- |
| 开始播放 / 换歌 | 立即发 `playing`（换歌时先不带封面，见 4.2） |
| 暂停 | 立即发一次 `paused`（`position` 为暂停时的位置）；暂停期间**不再发** |
| 继续播放 | 立即发 `playing` |
| 拖动进度 | 立即发。可以用 AIMP 的事件，也可以每次采样比较“实际进度”和“上次进度 + 经过时间”，相差超过 3 秒就算拖动 |
| 封面上传确认 | 立即补发一条带 `cover_hash` 的 |
| 停止、关闭 AIMP、插件卸载 | 如果之前在 `playing`，发一次 `paused`（推算好当时的位置） |
| 播放中没有变化 | 每 **120 秒**发一次心跳（就是再发一次当前状态），`ttl = 300` |

优先用 AIMP SDK 的播放器事件（播放状态变化、换曲、进度变化）触发；再配一个约 5 秒一次的轻量定时检查兜底。

**完成标准**：AIMP 换歌、暂停、拖动后，网页 **5 秒内**更新；暂停后显示“已暂停”，1 分钟后消失。

## 7. 参考：Mac 端的同类实现

Mac 小工具（Swift）里音乐上报的逻辑可以直接照搬到插件里，伪代码如下：

```text
last = null; lastSent = null

on sample(state):            # state 为 null 表示没在放 / AIMP 已关闭
  if state == null:
    if last != null and last.playing:
      last.position = min(last.position + (now - lastSent), last.duration)
      last.playing = false
      send(last)             # 补一条 paused
    return
  if last == null: send(state); return
  changed  = state.title != last.title or state.artist != last.artist
             or state.playing != last.playing or state.cover_hash != last.cover_hash
  expected = last.playing ? last.position + (now - lastSent) : last.position
  seeked   = abs(state.position - expected) > 3
  heartbeat = state.playing and now - lastSent >= 120
  if changed or seeked or heartbeat: send(state)

send(s): last = s; lastSent = now; 入队 → 后台线程发送
```

## 8. 隐私

- 只上报第 4 节列出的字段。**不要**上报文件路径、播放列表、用户名、机器名。
- 设备令牌只存在本机配置文件里。

## 9. 开发阶段怎么测试

Tunnel 配好之前，用一个模拟服务器测插件。随便用什么写，满足这几点即可：

- `POST /presence/api/signal`：检查 `Authorization`，打印收到的 JSON，返回 `{"ok": true, "accepted": N}`。
- `HEAD` / `PUT /presence/api/media/{sha256}`：按 4.2 的规则校验哈希、格式、大小，把图片存到临时目录，方便打开看对不对。
- 能临时让它返回 `401` / `500` / 断开，用来测离线队列和重试。

插件里把接口地址填成 `http://127.0.0.1:<端口>/presence/api`。

自查清单：

- [ ] 播放、暂停、继续、换歌、拖动、停止、关闭 AIMP 都按第 6 节发出了正确的信号
- [ ] 播放中每 2 分钟一次心跳，暂停中没有心跳
- [ ] 同一张封面只上传一次；重启 AIMP 后先 `HEAD`，不重复 `PUT`
- [ ] 中文、日文、emoji 歌名正常（JSON 用 UTF-8）
- [ ] 服务器关掉时信号进队列，重新打开后按原时间补发；`401` 时不死循环重试
- [ ] 网络很慢或断开时 AIMP 界面不卡

## 10. 联调（Tunnel 配好之后，需要用户配合）

1. 用户在服务器 Mac 上生成令牌：
   `cd server && uv run python -c "import secrets; print(secrets.token_urlsafe(32))"`，
   填到 `config/settings.toml` 的 `[devices]` → `windows = "…"`，然后在“终端”里按 Ctrl+C、重新运行 `scripts/start.sh`。
2. 插件里填接口地址 `https://api.ignat.ai/presence/api` 和这个令牌。
3. 先在 Windows 上确认能连通（`curl.exe` 是 Windows 10 起自带的）：

   ```
   curl.exe -i https://api.ignat.ai/presence/api/health
   curl.exe -X POST https://api.ignat.ai/presence/api/signal -H "Authorization: Bearer <令牌>" -H "Content-Type: application/json" -d "{\"kind\":\"music\",\"ttl\":60,\"data\":{\"player\":\"aimp\",\"title\":\"test\",\"artist\":\"\",\"album\":\"\",\"state\":\"playing\",\"position\":0,\"duration\":200}}"
   ```

4. 在 AIMP 里放歌，打开 `GET {API}/now`，`side` 或 `main` 里应出现：

   ```json
   {
     "type": "activity", "category": "music", "tier": 4, "label": "正在听《晴天》",
     "card": {
       "title": "晴天", "artist": "周杰伦", "album": "叶惠美",
       "state": "playing", "position": 83.2, "duration": 269.0,
       "updated_at": 1792904523.7, "session_since": 1792903000.0,
       "cover": "https://api.ignat.ai/presence/api/media/3ba1…"
     }
   }
   ```

   有 `cover` 说明封面链路通了。
