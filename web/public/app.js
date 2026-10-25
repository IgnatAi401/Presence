// now.ignat.ai 公开状态页：先拉 /now 和 /today，再用 SSE 实时更新

const params = new URLSearchParams(location.search);
const LOCAL = ["localhost", "127.0.0.1"].includes(location.hostname);
const PROD_API = "https://api.ignat.ai/presence/api";
const API = params.get("api") || (LOCAL ? "http://127.0.0.1:8790/presence/api" : PROD_API);
// 服务器给的封面地址写的是正式域名；本地调试时改成实际连接的接口
const mediaUrl = (url) => (url.startsWith(`${PROD_API}/`) ? API + url.slice(PROD_API.length) : url);

const KICKER = {
  anchor: "此刻",
  activity: "正在",
  idle: "此刻",
};
const NAMES = {
  class: "上课", eat: "吃饭", sleep: "睡觉", office: "办公室", research: "科研", dev: "开发", game: "游戏", out: "出门",
  custom: "自定义", video: "看视频", browse: "上网", phone: "刷手机", music: "听歌",
};

const CARD_ROW = { music: "在听音乐", game: "在玩游戏" };
const VIDEO_SITES = { bilibili: "B站", youtube: "YouTube" };

const $ = (id) => document.getElementById(id);
const hue = (category) => `var(--c-${category}, var(--amber))`;

let state = null; // 最近一次 /now
let today = null; // 最近一次 /today
let receivedAt = 0; // 收到 state 时的本地时间（秒），用来推算进度
let serverOffset = 0; // 服务器时间 − 本地时间

const nowSec = () => Date.now() / 1000 + serverOffset;

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "style") Object.assign(el.style, v);
    else if (k.startsWith("--")) el.style.setProperty(k, v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) el.append(c);
  return el;
}

function dur(sec) {
  sec = Math.max(0, Math.floor(sec));
  const hh = Math.floor(sec / 3600);
  const mm = Math.floor((sec % 3600) / 60);
  if (hh) return `${hh}h${String(mm).padStart(2, "0")}m`;
  if (mm) return `${mm} 分钟`;
  return `${sec} 秒`;
}

const clock = (ts) =>
  new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });

// 连续进行的时长：听歌、看视频跨换歌 / 换视频累计；暂停时停在暂停那一刻
function sessionSec(card, t) {
  const end = card.state === "playing" ? t : card.updated_at || t;
  return end - card.session_since;
}

function metaText(item, t) {
  const parts = [];
  if (item.type === "anchor" && item.category === "class" && item.until) {
    parts.push(`还剩 ${dur(item.until - t)}`);
  } else if (item.category === "video" && item.card?.session_since) {
    parts.push(`已持续 ${dur(sessionSec(item.card, t))}`);
  } else if (item.since && !item.card?.session_since) {
    parts.push(`已持续 ${dur(t - item.since)}`);
  }
  if (item.detail) parts.push(item.detail);
  if (item.today_sec != null) {
    // 合并了活动的锚定状态（在办公室搞科研）：已持续是在办公室的时长，今日时长是科研的
    const what = item.activity ? `今日${NAMES[item.activity] || ""}` : "今日已";
    const live = item.slacking ? 0 : t - state.updated_at; // 摸鱼时正事时长不走
    parts.push(`${what} ${dur(item.today_sec + live)}`);
  }
  return parts;
}

// 播放位置（秒），按收到后经过的时间和倍速推算
function position(card, t) {
  let pos = card.position || 0;
  if (card.state === "playing") pos += (t - (card.updated_at || t)) * (card.rate || 1);
  return Math.min(card.duration, Math.max(0, pos));
}

const mmss = (sec) => {
  sec = Math.floor(sec);
  const s = String(sec % 60).padStart(2, "0");
  return sec >= 3600 ? `${Math.floor(sec / 3600)}:${String(Math.floor(sec / 60) % 60).padStart(2, "0")}:${s}`
    : `${Math.floor(sec / 60)}:${s}`;
};

function renderCard(item, t) {
  const c = item.card;
  if (!c) return null;
  const cover = h("div", { class: "cover", style: c.cover ? { backgroundImage: `url("${encodeURI(mediaUrl(c.cover))}")` } : {} });
  let title = item.label;
  let sub = "";
  let href = null;
  let badge = null;
  const web = c.kind === "web";
  if (web) {
    // Safari 当前网页：标题 + 域名 + 网站图标
    const site = c.domain.replace(/^www\./, "");
    title = c.title || site;
    sub = site;
    href = `https://${c.domain}`;
  } else if (item.category === "music") {
    title = c.title || "未知曲目";
    sub = [c.artist, c.album].filter(Boolean).join(" · ") + (c.state === "paused" ? " · 已暂停" : "");
    if (c.session_since) badge = `连续听歌 ${dur(sessionSec(c, t))}`;
  } else if (item.category === "video") {
    // 服务器还没查到标题时只有进度条
    title = c.title || item.label;
    const speed = c.rate && c.rate !== 1 ? `${c.rate}×` : null;
    sub = [c.owner || VIDEO_SITES[c.site], speed, c.state === "paused" ? "已暂停" : null].filter(Boolean).join(" · ");
    href = c.url || null;
  } else if (item.category === "game") {
    title = c.name || item.label;
    sub = `本次已玩 ${dur(t - (c.since || item.since))}`;
    href = c.appid ? `https://store.steampowered.com/app/${encodeURIComponent(c.appid)}` : null;
  }
  const pos = c.duration ? position(c, t) : null;
  const body = h("div", { class: "body" },
    h("div", { class: "t", title }, title),
    sub ? h("div", { class: "s", title: sub }, sub) : null,
    pos != null ? h("div", { class: "progress" }, h("i", { style: { width: `${((pos / c.duration) * 100).toFixed(2)}%` } })) : null,
    pos != null ? h("div", { class: "times" }, h("span", {}, mmss(pos)), h("span", {}, mmss(c.duration))) : null,
  );
  const kind = web ? " web" : item.category === "music" ? "" : " wide";
  const attrs = { class: `card${kind}`, "--hue": hue(item.category) };
  return href
    ? h("a", { ...attrs, href, target: "_blank", rel: "noopener" }, cover, body)
    : h("div", { ...attrs, class: `${attrs.class}${badge ? " has-badge" : ""}` }, cover, body,
      badge ? h("span", { class: "badge" }, badge) : null);
}

function renderMain(t) {
  const m = state.main;
  const screen = document.querySelector(".screen");
  screen.style.setProperty("--hue", m.type === "idle" ? "var(--dim)" : hue(m.category));
  $("kicker").textContent = m.type === "idle" ? (m.category === "offline" ? "OFFLINE" : "AWAY") : KICKER[m.type];
  const label = $("main-label");
  label.textContent = m.label;
  label.title = m.label; // 超过两行时被截断，悬停看全文
  label.classList.toggle("long", [...m.label].length > 14);
  const meta = $("main-meta");
  meta.replaceChildren();
  const parts = metaText(m, t);
  meta.append(parts.join(" · "));
  if (m.slacking) {
    meta.append(parts.length ? " · " : "", h("span", { class: "slack" }, `🐟 摸鱼中 ${dur(t - m.slacking.since)}`));
  }
  $("main-card").replaceChildren(renderCard(m, t) || "");
  document.title = m.type === "idle" ? "Now · Ignat" : `${m.label} · Now`;
}

function renderSide(t) {
  const list = $("side");
  list.replaceChildren(...state.side.map((item) => {
    const card = renderCard(item, t);
    const sub = [];
    if (item.slacking) sub.push(`🐟 摸鱼中 ${dur(t - item.slacking.since)}`);
    else if (item.card?.state === "paused") sub.push("已暂停");
    else if (item.category === "video" && item.card?.session_since) sub.push(dur(sessionSec(item.card, t)));
    else if (item.since && !item.card?.session_since) sub.push(dur(t - item.since));
    // 有卡片时歌名、游戏名在卡片里，上面这行只写类别
    const text = card ? CARD_ROW[item.category] || item.label
      : item.detail ? `${item.label} · ${item.detail}` : item.label;
    return h("li", { class: card ? "has-card" : null, "--hue": hue(item.category) },
      h("span", { class: "lbl", title: text }, text),
      sub.length ? h("span", { class: "sub" }, sub.join(" · ")) : null,
      card,
    );
  }));
}

function renderToday(t) {
  if (!today) return;
  const span = today.end - today.start;
  const bar = $("bar");
  const cursor = $("cursor");
  bar.replaceChildren(cursor, ...today.timeline.map((b) => h("span", {
    class: "blk",
    title: `${clock(b.start)}–${clock(b.end)} ${b.label}`,
    "--hue": hue(b.category),
    style: { left: `${((b.start - today.start) / span) * 100}%`, width: `${((b.end - b.start) / span) * 100}%` },
  })));
  cursor.style.left = `${Math.min(100, ((t - today.start) / span) * 100)}%`;
  $("today-date").textContent = today.date;

  const rows = [...Object.entries(today.totals.anchor), ...Object.entries(today.totals.activity)]
    .filter(([, sec]) => sec >= 60)
    .sort((a, b) => b[1] - a[1]);
  $("totals").replaceChildren(...rows.map(([cat, sec]) =>
    h("li", { "--hue": hue(cat) }, h("b", {}, NAMES[cat] || cat), h("span", {}, dur(sec)))));
}

function render() {
  if (!state) return;
  const t = nowSec();
  renderMain(t);
  renderSide(t);
  renderToday(t);
}

function setAir(mode) {
  $("air").dataset.state = mode;
  $("air-text").textContent = { live: "ON AIR", down: "NO SIGNAL", connecting: "TUNING" }[mode];
  $("notice").hidden = mode !== "down";
}

function accept(data) {
  const prevMain = state && `${state.main.category}|${state.main.label}`;
  state = data;
  receivedAt = Date.now() / 1000;
  serverOffset = data.updated_at - receivedAt;
  render();
  if (prevMain !== `${data.main.category}|${data.main.label}`) loadToday();
}

async function getJSON(path) {
  const r = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  return r.json();
}

async function loadToday() {
  try {
    today = await getJSON("/today");
    render();
  } catch {
    /* 时间线拉取失败不影响主状态 */
  }
}

async function loadNow() {
  try {
    accept(await getJSON("/now"));
    return true;
  } catch {
    setAir("down");
    return false;
  }
}

let es = null;
let retry = 2000;

function connect() {
  es?.close();
  es = new EventSource(`${API}/stream`);
  es.addEventListener("now", (ev) => {
    retry = 2000;
    setAir("live");
    accept(JSON.parse(ev.data));
  });
  es.onerror = () => {
    setAir("down");
    es.close();
    // 断线后自己控制重连节奏，重连前先拉一次 /now
    setTimeout(async () => {
      await loadNow();
      connect();
    }, retry);
    retry = Math.min(retry * 2, 60000);
  };
}

await loadNow();
await loadToday();
connect();
setInterval(render, 1000);
setInterval(loadToday, 60000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    loadNow();
    loadToday();
  }
});
