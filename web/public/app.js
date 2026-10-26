// now.ignat.ai 公开状态页：先拉 /now 和 /today，再用 SSE 实时更新

import { $, API, dur, getJSON, h, hue, mediaUrl, timelineBar, totalsList } from "./lib.js";

const KICKER = {
  anchor: "此刻",
  activity: "正在",
  idle: "此刻",
};
// 类别简称、App 图标、网站和播放器的名字都由服务器按 rules.yaml 给出，这里不再另存一份
const CARD_ROW = { music: "在听音乐", game: "在玩游戏" };

let state = null; // 最近一次 /now
let today = null; // 最近一次 /today
let receivedAt = 0; // 收到 state 时的本地时间（秒），用来推算进度
let serverOffset = 0; // 服务器时间 − 本地时间

const nowSec = () => Date.now() / 1000 + serverOffset;

const phoneIcons = new Map();
// 只用同站静态图标（服务器给的 icon），不让浏览器请求第三方图标服务
function phoneIcon(item, slot) {
  const slug = item.category === "phone" && item.icon;
  if (!slug) return null;
  const key = `${slot}:${slug}`;
  let icon = phoneIcons.get(key);
  if (!icon) {
    icon = h("img", { class: "app-icon", src: `/icons/apps/${slug}.png`, alt: "", title: item.app,
      "aria-hidden": "true", width: 128, height: 128, decoding: "async" });
    icon.addEventListener("error", () => { icon.hidden = true; }, { once: true });
    phoneIcons.set(key, icon);
    if (phoneIcons.size > 50) phoneIcons.delete(phoneIcons.keys().next().value);
  }
  return icon;
}

// 看完了在看评论区：视频暂停着，但人还在
const readingComments = (card) => card?.viewing === "comments";

// 连续进行的时长：听歌、看视频跨换歌 / 换视频累计；暂停时停在暂停那一刻（在看评论时接着走）
function sessionSec(card, t) {
  const end = card.state === "playing" || readingComments(card) ? t : card.updated_at || t;
  return end - card.session_since;
}

function metaText(item, t) {
  const parts = item.detail ? [item.detail] : []; // 工作区名放最前面
  if (item.category === "phone") parts.push("iPhone");
  if (item.type === "anchor" && item.category === "class" && item.until) {
    parts.push(`还剩 ${dur(item.until - t)}`);
  } else if (item.category === "video" && item.card?.session_since) {
    parts.push(`已持续 ${dur(sessionSec(item.card, t))}`);
  } else if (item.since && !item.card?.session_since) {
    parts.push(`已持续 ${dur(t - item.since)}`);
  }
  if (item.today_sec != null) {
    // 合并了活动的锚定状态（在办公室搞科研）：已持续是在办公室的时长，今日时长是科研的
    const what = item.activity ? `今日${item.activity_name || ""}` : "今日已";
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

// 右上角标签大约多宽（12px 等宽字体：英文数字约 0.6em，中文 1em），给标题留出位置
const badgeWidth = (text) => Math.ceil([...text].reduce((w, ch) => w + (ch.charCodeAt(0) > 255 ? 12 : 7.2), 0)) + 16;

// 每秒重画时复用同一个封面节点：新建的图片要重新解码，会一闪一闪
const covers = new Map();
function coverNode(url, player, playerName) {
  const key = `${url || ""}|${player || ""}`;
  let el = covers.get(key);
  if (!el) {
    el = h("div", { class: "cover", style: url ? { backgroundImage: `url("${encodeURI(mediaUrl(url))}")` } : {} },
      player ? h("img", { class: `player ${player}`, src: `/icons/${player.replace("_", "-")}.svg`, alt: playerName, title: playerName }) : null);
    covers.set(key, el);
    if (covers.size > 50) covers.delete(covers.keys().next().value); // 只留最近建的 50 个
  }
  return el;
}

function renderCard(item, t) {
  const c = item.card;
  if (!c) return null;
  const player = item.category === "music" ? c.player_name : null;
  const cover = coverNode(c.cover, player && c.player, player);
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
    if (c.session_since) badge = [player, dur(sessionSec(c, t))].filter(Boolean).join(" · ");
  } else if (item.category === "video") {
    // 服务器还没查到标题时只有进度条
    title = c.title || item.label;
    const speed = c.rate && c.rate !== 1 ? `${c.rate}×` : null;
    const status = readingComments(c) ? "在看评论" : c.state === "paused" ? "已暂停" : null;
    sub = [c.owner || c.site_name, speed, status].filter(Boolean).join(" · ");
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
    : h("div", { ...attrs, class: `${attrs.class}${badge ? " has-badge" : ""}`, "--badge-w": badge ? `${badgeWidth(badge)}px` : null }, cover, body,
      badge ? h("span", { class: "badge" }, badge) : null);
}

function renderMain(t) {
  const m = state.main;
  const screen = document.querySelector(".screen");
  screen.style.setProperty("--hue", m.type === "idle" ? "var(--dim)" : hue(m.category));
  $("kicker").textContent = m.category === "out" ? "出门走走" : m.type === "idle" ? (m.category === "offline" ? "OFFLINE" : "AWAY") : KICKER[m.type];
  const label = $("main-label");
  label.replaceChildren(...[phoneIcon(m, "main"), m.label].filter((child) => child != null));
  label.title = m.label; // 超过两行时被截断，悬停看全文
  const labelLength = [...m.label].length;
  label.classList.toggle("long", labelLength > 14 || (m.category === "phone" && labelLength >= 10));
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
  list.replaceChildren(...state.side.map((item, index) => {
    const card = renderCard(item, t);
    const sub = item.category === "phone" ? ["iPhone"] : [];
    if (item.slacking) sub.push(`🐟 摸鱼中 ${dur(t - item.slacking.since)}`);
    else if (item.card?.state === "paused" && !readingComments(item.card)) sub.push("已暂停");
    else if (item.category === "video" && item.card?.session_since) sub.push(dur(sessionSec(item.card, t)));
    else if (item.since && !item.card?.session_since) sub.push(dur(t - item.since));
    // 有卡片时歌名、游戏名在卡片里，上面这行只写类别
    const text = card ? CARD_ROW[item.category] || item.label
      : item.detail ? `${item.detail} · ${item.label}` : item.label;
    return h("li", { class: card ? "has-card" : null, "--hue": hue(item.category) },
      h("span", { class: "lbl", title: text }, phoneIcon(item, `side-${index}`), text),
      sub.length ? h("span", { class: "sub" }, sub.join(" · ")) : null,
      card,
    );
  }));
}

function renderToday(t) {
  if (!today) return;
  timelineBar($("bar"), today, today.timeline);
  $("cursor").style.left = `${Math.min(100, ((t - today.start) / (today.end - today.start)) * 100)}%`;
  $("today-date").textContent = today.date;
  totalsList($("totals"), today.totals, today.names);
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
