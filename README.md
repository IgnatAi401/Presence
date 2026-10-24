# Presence

在 [now.ignat.ai](https://now.ignat.ai) 实时显示我此刻大致在做什么，并在服务器上长期记录每天的活动时间。

| 目录 | 内容 |
| --- | --- |
| `server/` | FastAPI 后端（uv 管理）：信号接收、状态引擎、SQLite、公开接口、SSE 推送 |
| `web/` | now.ignat.ai 静态站（Cloudflare Worker `presence-web`，pnpm 管理 wrangler） |
| `agents/` | 各设备上的上报小工具（待开发） |
| `shortcuts/` | iPhone 快捷指令（待开发） |

## 服务器

```bash
cd server
uv sync --locked
cp config/settings.example.toml config/settings.toml   # 填写设备令牌；settings.toml 不进 Git
scripts/start.sh                                        # 监听 127.0.0.1:8790，Ctrl+C 停止
uv run pytest                                           # 测试
```

- 分类规则和活动等级：`server/config/rules.yaml`，改完重启即可。
- 读取课表需要日历权限，先单独运行一次，在弹窗里允许：`uv run python -m presence.sources.calendar 课程表`
- 接口：`/presence/api/signal`（设备令牌）、`now`、`stream`（SSE）、`today`、`health`。

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
