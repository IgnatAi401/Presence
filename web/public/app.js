// now.ignat.ai 公开状态页：先拉 /now 和 /today，再用 SSE 实时更新

import { $, API, clock, dur, getJSON, h, hue, imageHue, mediaUrl, patch, timelineBar, totalsList } from "./lib.js";
import { setBgLive } from "./bg.js";

const KICKER = {
  anchor: "此刻",
  activity: "正在",
  idle: "此刻",
};
// 类别简称、App 图标、网站和播放器的名字都由服务器按 rules.yaml 给出，这里不再另存一份

let state = null; // 最近一次 /now
let today = null; // 最近一次 /today
let receivedAt = 0; // 收到 state 时的本地时间（秒），用来推算进度
let serverOffset = 0; // 服务器时间 − 本地时间
let todayDirty = false;

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

// 卡片主题色：此刻视图整体是蓝色系（见 style.css 的 --bl-*）。
// 有封面、App 图标时按图片的主色，但只在主色本身偏蓝紫（色相 180°–290°）或是白色时才用；
// 否则按类别取一种蓝。离开、离线保持灰色
const BLUE = {
  dev: "cyan", office: "cyan",
  class: "blue", research: "blue", browse: "blue",
  game: "indigo", video: "indigo", phone: "indigo",
  sleep: "violet", music: "violet", custom: "violet",
  eat: "ice", out: "ice",
};
function cardHue(item) {
  const c = item.card;
  const url = item.category === "phone" && item.icon ? `/icons/apps/${item.icon}.png`
    : c?.cover ? mediaUrl(c.cover) : null;
  const img = imageHue(url, () => render());
  const deg = img?.startsWith("hsl(") ? Number(img.slice(4).split(" ")[0]) : null;
  if (img && (deg == null || (deg >= 180 && deg <= 290))) return img;
  return BLUE[item.category] ? `var(--bl-${BLUE[item.category]})` : hue(item.category);
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
function coverNode(url, player, playerName, cls = "cover") {
  const key = `${cls}|${url || ""}|${player || ""}`;
  let el = covers.get(key);
  if (!el) {
    el = h("div", { class: cls, style: url ? { backgroundImage: `url("${encodeURI(mediaUrl(url))}")` } : {} },
      player ? h("img", { class: `player ${player}`, src: `/icons/${player.replace("_", "-")}.svg`, alt: playerName, title: playerName }) : null);
    covers.set(key, el);
    if (covers.size > 50) covers.delete(covers.keys().next().value); // 只留最近建的 50 个
  }
  return el;
}

// inSide：伴随状态里的大卡片，右上角注明来源和连续时长（主状态的时长写在大字下面）
function renderCard(item, t, inSide = false) {
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
    if (inSide && c.session_since) badge = [c.site_name, dur(sessionSec(c, t))].filter(Boolean).join(" · ");
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
  const attrs = { class: `card${kind}${badge ? " has-badge" : ""}`, "--hue": cardHue(item),
    "--badge-w": badge ? `${badgeWidth(badge)}px` : null };
  const children = [cover, body, badge ? h("span", { class: "badge" }, badge) : null];
  return href
    ? h("a", { ...attrs, href, target: "_blank", rel: "noopener" }, ...children)
    : h("div", attrs, ...children);
}

function renderMain(t) {
  const m = state.main;
  // 主标题说明正在做什么，具体网页标题和域名放在下方卡片。
  const mainLabel = m.category === "browse" && m.card?.kind === "web" ? "网上冲浪" : m.label;
  const hero = document.querySelector(".hero");
  const color = m.type === "idle" ? "var(--dim)" : cardHue(m);
  hero.style.setProperty("--hue", color);
  // 有封面时在主状态背后铺一层模糊的封面
  const art = m.card?.cover && m.card.kind !== "web" ? `url("${encodeURI(mediaUrl(m.card.cover))}")` : null;
  if (art) hero.style.setProperty("--art", art);
  else hero.style.removeProperty("--art");
  setBgLive(m.category !== "offline");
  $("clock").textContent = clock(t);
  $("kicker").textContent = m.category === "out" ? "出门走走" : m.type === "idle" ? (m.category === "offline" ? "OFFLINE" : "AWAY") : KICKER[m.type];
  const label = $("main-label");
  label.replaceChildren(...[phoneIcon(m, "main"), mainLabel].filter((child) => child != null));
  label.title = mainLabel; // 超过两行时被截断，悬停看全文
  const labelLength = [...mainLabel].length;
  label.classList.toggle("long", labelLength > 14 || (m.category === "phone" && labelLength >= 10));
  const meta = $("main-meta");
  meta.replaceChildren();
  const parts = metaText(m, t);
  meta.append(parts.join(" · "));
  if (m.slacking) {
    meta.append(parts.length ? " · " : "", h("span", { class: "slack" }, `🐟 摸鱼中 ${dur(t - m.slacking.since)}`));
  }
  patch($("main-card"), [renderCard(m, t)]);
  // 切到历史视图时标题归 views.js 管
  if (document.documentElement.dataset.view === "now") document.title = m.type === "idle" ? "Now · Ignat" : `${m.label} · Now`;
}

// 类别简称：/today 里带着服务器给的名字，还没拉到时先用标签
const catName = (item) => today?.names?.[item.category] || item.label;

// 伴随状态的一张小卡片：类别 · 时长 / 标题 / 副标题，左边缩略图，底边进度
function renderTile(item, index, t) {
  const c = item.card || {};
  const time = item.category === "video" && c.session_since ? sessionSec(c, t)
    : item.category === "music" && c.session_since ? sessionSec(c, t)
    : item.since ? t - item.since : null;
  let cat = catName(item);
  let title = item.detail || item.label;
  let sub = item.detail ? item.label : "";
  let href = null;
  let thumb = null;
  let wide = false;
  const paused = c.state === "paused" && !readingComments(c);
  if (c.kind === "web") {
    const site = c.domain.replace(/^www\./, "");
    title = c.title || site;
    sub = site;
    href = `https://${c.domain}`;
    if (c.cover) thumb = h("img", { class: "ic", src: mediaUrl(c.cover), alt: "", style: { width: "24px", height: "24px" } });
  } else if (item.category === "music") {
    cat = [cat, c.player_name].filter(Boolean).join(" · ");
    title = c.title || item.label;
    sub = [c.artist, c.album].filter(Boolean).join(" · ") + (paused ? " · 已暂停" : "");
    thumb = coverNode(c.cover, c.player_name && c.player, c.player_name, "thumb");
  } else if (item.category === "video") {
    cat = [cat, c.site_name].filter(Boolean).join(" · ");
    title = c.title || item.label;
    const speed = c.rate && c.rate !== 1 ? `${c.rate}×` : null;
    const status = readingComments(c) ? "在看评论" : paused ? "已暂停" : null;
    sub = [c.owner, speed, status].filter(Boolean).join(" · ");
    href = c.url || null;
    if (c.cover) thumb = coverNode(c.cover, null, null, "thumb");
    wide = Boolean(c.cover);
  } else if (item.category === "game") {
    title = c.name || item.label;
    sub = "Steam";
    href = c.appid ? `https://store.steampowered.com/app/${encodeURIComponent(c.appid)}` : null;
  } else if (item.category === "phone") {
    title = item.app || item.label;
    sub = "iPhone";
    thumb = phoneIcon(item, `side-${index}`);
  }
  if (item.slacking) sub = [sub, `🐟 摸鱼中 ${dur(t - item.slacking.since)}`].filter(Boolean).join(" · ");
  if (!thumb?.classList.contains("thumb")) thumb = h("div", { class: "thumb" }, thumb || [...cat][0]);
  const pos = c.duration ? position(c, t) : null;
  const inner = [
    thumb,
    h("div", { class: "info" },
      h("div", { class: "row" }, h("span", { class: "cat" }, cat), time != null ? h("span", { class: "time" }, dur(time)) : null),
      h("div", { class: "ttl", title }, title),
      sub ? h("div", { class: "sub", title: sub }, sub) : null),
    pos != null ? h("i", { class: "prog", style: { width: `${((pos / c.duration) * 100).toFixed(2)}%` } }) : null,
  ];
  return h("li", { class: ["tile", wide && "wide", paused && "paused"].filter(Boolean).join(" "), "--hue": cardHue(item) },
    href ? h("a", { class: "in", href, target: "_blank", rel: "noopener" }, ...inner) : h("div", { class: "in" }, ...inner));
}

// 音乐、视频用大卡片（封面、进度）；手机 App 等小卡片排在前面，长条在下面
const isBig = (item) => ["music", "video"].includes(item.category) && item.card && item.card.kind !== "web";

function renderSide(t) {
  $("side-wrap").hidden = !state.side.length;
  const items = state.side.map((item, index) => ({ item, index }));
  const ordered = [...items.filter(({ item }) => !isBig(item)), ...items.filter(({ item }) => isBig(item))];
  patch($("side"), ordered.map(({ item, index }) => isBig(item)
    ? h("li", { class: "tile big", "--hue": cardHue(item) }, renderCard(item, t, true))
    : renderTile(item, index, t)));
}

function renderToday(t) {
  if (!today) return;
  // 秒钟和播放进度每秒走；历史片段只在接口返回新数据后重建。
  if (todayDirty) {
    timelineBar($("bar"), today, today.timeline);
    $("today-date").textContent = today.date;
    totalsList($("totals"), today.totals, today.names);
    todayDirty = false;
  }
  $("cursor").style.left = `${Math.min(100, ((t - today.start) / (today.end - today.start)) * 100)}%`;
}

function render() {
  if (!state || document.hidden) return;
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
    todayDirty = true;
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
addEventListener("viewchange", (ev) => ev.detail === "now" && render());
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    loadNow();
    loadToday();
  }
});
