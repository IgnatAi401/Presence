# Presence

在 [now.ignat.ai](https://now.ignat.ai) 实时显示我此刻大致在做什么，并在服务器上长期记录每天的活动时间。

| 目录 | 内容 |
| --- | --- |
| `server/` | FastAPI 后端（uv 管理）：信号接收、状态引擎、SQLite、公开接口、SSE 推送 |
| `web/` | now.ignat.ai 静态站（Cloudflare Worker `presence-web`，pnpm 管理 wrangler） |
| `agents/mac/` | Mac 菜单栏小工具（Swift，见其 README） |
| `shortcuts/` | iPhone 快捷指令：睡觉、起床、吃饭、办公室、出去玩和自定义状态，见[安装说明](shortcuts/README.md) |

## 服务器

```bash
cd server
uv sync --locked
cp config/settings.example.toml config/settings.toml   # 填写设备令牌；settings.toml 不进 Git
scripts/start.sh                                        # 监听 127.0.0.1:8790，Ctrl+C 停止
uv run pytest                                           # 测试
```

- 分类规则、活动等级和各处显示用的名字 / 图标（手机 App、视频网站、播放器）：`server/config/rules.yaml`，改完重启即可，网页和小工具不用改。哪个项目算科研、哪个算开发在 Mac 小工具的 设置 → 项目 里选，随信号上报。
- 各设备能上报哪些信号、旧写法怎么兼容：见 [docs/signals.md](docs/signals.md)。
- 读取课表需要日历权限，先单独运行一次，在弹窗里允许：`uv run python -m presence.sources.calendar 课程表`
- 公开接口：`/presence/api/signal`、`PUT media/{sha256}`（设备令牌）、`media/{sha256}`（封面图片，存在 `data/media/`）、`now`、`stream`（SSE）、`today`、`day?date=`、`history?from=&to=`（每日各类别时长、正事时长、游戏和听歌榜，默认最近半年）、`health`。
- 私有接口 `private/*`：`day?date=`（完整片段、播放和浏览记录）、`summary?from=&to=&group=category|project|site|video|track|game|app&during=class`、`focus?from=&to=`、`export?from=&to=&format=csv|json`。只接受带正确 `X-Ignat-Gate` 请求头（`settings.toml` 的 `gate_secret`，与 ignat-auth 的 `ORIGIN_SECRET` 相同）的请求，否则 404；`gate_secret` 空着时私有接口全部关闭。
- 每天第一次推进时把数据库备份到 `data/backups/presence-YYYYMMDD.db`，保留 30 份；原始信号保留 30 天。

模拟一条上报：

```bash
curl -X POST http://127.0.0.1:8790/presence/api/signal \
  -H "Authorization: Bearer <设备令牌>" -H 'Content-Type: application/json' \
  -d '{"signals":[{"kind":"desktop","data":{"bundle_id":"com.mathworks.matlab","idle_sec":1}}]}'
```

## 网页

```bash
cd web
pnpm install --frozen-lockfile
pnpm dev        # http://localhost:8788，自动连接本机 127.0.0.1:8790 的服务器
pnpm deploy     # 部署到 now.ignat.ai
```

本地调试时，需要在 `settings.toml` 的 `extra_origins` 里加入 `http://localhost:8788`。也可以用 `?api=` 参数指定接口地址。

大量数据预览：在 `web/` 运行 `pnpm perf`，打开 `http://127.0.0.1:8790`。这个独立的本地模拟站点包含半年历史、每天 2,000 个时间片段及各 2,000 条播放和浏览记录，不需要后端、登录或真实数据库。可用 `pnpm perf --rows=10000` 增加每天的记录数；按 Ctrl+C 停止。详细历史中的播放和浏览记录每批显示 100 条，点击列表下方的按钮继续显示。

| 页面 | 内容 |
| --- | --- |
| `/` | 此刻状态、今日时间线 |
| `/history` | 公开历史：正事热力图、任选一天的时间线、按周 / 月趋势、游戏和听歌榜 |
| `/me` | 私有回看（需在 401.ignat.ai 登录）：每条线一行的完整时间线、播放和浏览记录、专注度、排行（可只看上课 / 吃饭时）、导出 |

本地调试 `/me`：在本机的 `settings.toml` 里设 `dev_private = true`（不经过 Cloudflare 的请求不检查网关密钥），**服务器 Mac 上必须保持 false**。
