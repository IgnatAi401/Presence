// 独立的本地压力场景，不连接真实服务、不写数据库。
// pnpm perf（Ctrl+C 停止）；可用 --port=8791 --root=/tmp/baseline/public 做对照。
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const args = Object.fromEntries(process.argv.slice(2).map(x => x.replace(/^--/, '').split('=')));
const root = resolve(args.root || fileURLToPath(new URL('../public/', import.meta.url)));
const port = Number(args.port || 8790);
const count = Math.floor(Math.min(10000, Math.max(1, Number(args.rows) || 2000)));
const names = { research: '科研', dev: '开发', browse: '浏览', video: '视频', music: '听歌', game: '游戏', phone: '手机', office: '办公室' };
const cats = Object.keys(names).slice(0, 7);
const date = '2026-10-26';
const start = Date.parse(`${date}T04:00:00+08:00`) / 1000;
function day(iso = date) {
  const a = Date.parse(`${iso}T04:00:00+08:00`) / 1000;
  const timeline = Array.from({ length: count }, (_, i) => ({ start: a + i * 86400 / count, end: a + (i + 1) * 86400 / count, category: cats[i % cats.length], label: names[cats[i % cats.length]] }));
  return { date: iso, start: a, end: a + 86400, names,
    timeline, totals: { anchor: { office: 28800 }, activity: Object.fromEntries(cats.map(c => [c, 7200])) },
    segments: timeline.map(b => ({ ...b, line: 'activity', source: '压力模拟', detail: '本地假数据' })),
    media: timeline.map((b, i) => ({ ...b, type: i % 2 ? 'music' : 'video', key: `youtube:demo${i}`, title: `模拟播放记录 ${i + 1}`, artist: '模拟作者', player: 'youtube', seconds: b.end - b.start })),
    web: timeline.map((b, i) => ({ ...b, domain: 'example.com', title: `模拟浏览记录 ${i + 1}` })) };
}
const d = day();
const days = Array.from({ length: 182 }, (_, i) => ({ date: new Date((start - (181 - i) * 86400) * 1000).toISOString().slice(0, 10), totals: d.totals, serious_sec: 14400 + i % 7 * 1800, focus_sec: 14400, longest_sec: 5400, longest_start: start, interruptions: 3, slack_sec: 1200 }));
const ranks = Array.from({ length: 100 }, (_, i) => ({ key: `模拟条目${i}`, title: `模拟条目 ${i + 1}`, artist: '模拟作者', category: 'browse', seconds: (100 - i) * 600, plays: 100 - i, visits: 100 - i }));
const now = () => ({ updated_at: Date.now() / 1000, main: { type: 'activity', category: 'research', label: '科研', detail: '大量数据压力测试', since: Date.now() / 1000 - 3600, today_sec: 14400 }, side: cats.slice(1).map(category => ({ type: 'activity', category, label: names[category], since: Date.now() / 1000 - 600 })) });
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2' };
const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${port}`);
  if (url.pathname.startsWith('/presence/api/')) {
    const path = url.pathname.replace('/presence/api', '');
    res.setHeader('Access-Control-Allow-Origin', '*');
    if (path === '/stream') {
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store' });
      const send = () => res.write(`event: now\ndata: ${JSON.stringify(now())}\n\n`);
      send(); const timer = setInterval(send, 15000); req.on('close', () => clearInterval(timer)); return;
    }
    const data = path === '/now' ? now()
      : ['/day', '/private/day', '/today'].includes(path) ? (url.searchParams.has('date') ? day(url.searchParams.get('date')) : d)
      : path === '/history' ? { days, names, games: ranks, music: ranks }
      : path === '/private/focus' ? { days: days.filter(d => d.date >= (url.searchParams.get('from') || '') && d.date <= (url.searchParams.get('to') || date)), channels: ranks.slice(0, 10) }
      : path === '/private/summary' ? { items: ranks } : null;
    res.writeHead(data ? 200 : 404, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(data)); return;
  }
  try {
    const path = resolve(root, `.${decodeURIComponent(url.pathname)}`);
    if (path !== root && !path.startsWith(root + sep)) throw new Error('outside root');
    let file = extname(path) ? path : resolve(path, 'index.html');
    // 和 wrangler 的 single-page-application 一样：没有对应文件的页面地址（如 /history）给首页
    const body = await readFile(file).catch(e => { if (extname(path)) throw e; file = resolve(root, 'index.html'); return readFile(file); });
    res.writeHead(200, { 'Content-Type': mime[extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store' }); res.end(body);
  } catch { res.writeHead(404); res.end('Not found'); }
});
server.listen(port, '127.0.0.1', () => console.log(`模拟站点 http://127.0.0.1:${port} · ${count} 时间片段 / ${count} 播放 / ${count} 浏览 · Ctrl+C 停止`));
