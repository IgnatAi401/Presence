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
- 接口：`/presence/api/signal`、`PUT media/{sha256}`（设备令牌）、`media/{sha256}`（封面图片，存在 `data/media/`）、`now`、`stream`（SSE）、`today`、`health`。

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
