// now.ignat.ai/me 私有回看页：数据都来自 /private/*（经 401.ignat.ai 登录），页面本身不含数据

import { $, API, addDays, clock, dur, enableTips, getJSON, h, hue, rankList, totalsList } from "/lib.js";

const SITES = { bilibili: "B站", youtube: "YouTube" };
const MEDIA_KIND = { video: "视频", music: "歌", game: "游戏", app: "手机" };
const GROUP_HUE = { project: "dev", site: "browse", video: "video", track: "music", game: "game", app: "phone" };

let today = null; // 服务器认为的“今天”（按凌晨 4 点切分）
let names = {};
let range = { from: null, to: null };
let group = "category";

class Unauthorized extends Error {}

async function priv(path, init = {}) {
  try {
    return await getJSON(`/private${path}`, { credentials: "include", ...init });
  } catch (err) {
    if (err.status === 401 && err.body?.login) throw Object.assign(new Unauthorized(), { login: err.body.login });
    throw err;
  }
}

function showGate(err) {
  $("app").hidden = true;
  $("gate").hidden = false;
  if (err instanceof Unauthorized) {
    const url = `${err.login}?return=${encodeURIComponent(location.href)}`;
    $("gate-text").textContent = "需要先登录。";
    $("login").href = url;
    $("login").hidden = false;
    location.replace(url);
  } else {
    $("gate-text").textContent = "状态服务暂时离线，或私有接口还没开通。稍后刷新试试。";
  }
}

// -- 某一天

function videoUrl(key, page) {
  const [site, id] = key.split(":");
  if (site === "bilibili") {
    if (/^(ep|ss)\d+$/.test(id)) return `https://www.bilibili.com/bangumi/play/${id}`;
    return `https://www.bilibili.com/video/${id}${page > 1 ? `?p=${page}` : ""}`;
  }
  if (site === "youtube") return `https://www.youtube.com/watch?v=${encodeURIComponent(id)}`;
  return null;
}

function describe(s) {
  const bits = [names[s.category] || s.category];
  for (const v of [s.course, s.project, s.domain, s.detail]) if (v && !bits.includes(v)) bits.push(v);
  if (s.slacking) bits.push("摸鱼中");
  if (s.private) bits.push("私密");
  if (s.source) bits.push(s.source);
  return bits.join(" · ");
}

function lane(title, day, blocks, cls = "") {
  const span = day.end - day.start;
  return h("div", { class: `lane ${cls}` }, h("b", { title }, title),
    h("div", { class: "bar" }, blocks.map((b) => h("span", {
      class: `blk${b.slacking ? " slack" : ""}`,
      "--hue": hue(b.category),
      "data-tip": `${clock(b.start)}–${clock(b.end)}（${dur(b.end - b.start)}）${b.tip}`,
      style: { left: `${((b.start - day.start) / span) * 100}%`, width: `${((b.end - b.start) / span) * 100}%` },
    }))));
}

function renderLanes(d) {
  const segs = d.segments;
  const rows = [lane("主状态", d, d.timeline.map((b) => ({ ...b, tip: ` ${b.label}` })), "main")];
  const anchors = segs.filter((s) => s.line === "anchor");
  if (anchors.length) rows.push(lane("锚定", d, anchors.map((s) => ({ ...s, tip: ` ${describe(s)}` }))));
  // 每种活动一行，按当天时长从多到少
  const cats = [...new Set(segs.filter((s) => s.line === "activity").map((s) => s.category))]
    .sort((a, b) => (d.totals.activity[b] || 0) - (d.totals.activity[a] || 0));
  for (const c of cats) {
    rows.push(lane(names[c] || c, d, segs.filter((s) => s.line === "activity" && s.category === c)
      .map((s) => ({ ...s, tip: ` ${describe(s)}` }))));
  }
  $("lanes").replaceChildren(...rows);
  totalsList($("totals"), d.totals, d.names);
}

function logItem(start, end, what, extra) {
  return h("li", {}, h("time", {}, `${clock(start)}–${clock(end)}`), h("span", { class: "what" }, what), h("span", { class: "d" }, extra));
}

function renderLogs(d) {
  const media = d.media.map((m) => {
    const title = m.title || (m.type === "video" ? m.key.split(":")[1] : m.key);
    const url = m.type === "video" ? videoUrl(m.key, m.page) : null;
    const sub = m.type === "video" ? [m.artist, SITES[m.player]].filter(Boolean).join(" · ")
      : m.type === "music" ? m.artist : m.type === "app" ? "iPhone" : null;
    return logItem(m.start, m.end, [
      h("span", { class: "tag", "--hue": hue(m.type === "app" ? "phone" : m.type) }, MEDIA_KIND[m.type]),
      url ? h("a", { href: url, target: "_blank", rel: "noopener", title }, title) : h("span", { title }, title),
      sub ? h("small", {}, sub) : null,
    ], dur(m.seconds));
  });
  $("media").replaceChildren(...(media.length ? media.reverse() : [h("li", { class: "empty" }, "这天没有播放记录")]));
  const web = d.web.map((w) => logItem(w.start, w.end, [
    h("a", { href: `https://${w.domain}`, target: "_blank", rel: "noopener" }, w.title || w.domain),
    h("small", {}, w.domain.replace(/^www\./, "") + (w.private ? " · 私密" : "")),
  ], dur(w.end - w.start)));
  $("web").replaceChildren(...(web.length ? web.reverse() : [h("li", { class: "empty" }, "这天没有浏览记录")]));
}

async function loadDay(date) {
  const d = await priv(date ? `/day?date=${date}` : "/day");
  names = d.names;
  $("date").value = d.date;
  $("next").disabled = today != null && d.date >= today;
  renderLanes(d);
  renderLogs(d);
  return d;
}

// -- 区间

function qs(extra = {}) {
  return new URLSearchParams({ from: range.from, to: range.to, ...extra }).toString();
}

async function loadFocus() {
  const f = await priv(`/focus?${qs()}`);
  const sum = (k) => f.days.reduce((s, d) => s + d[k], 0);
  const worked = f.days.filter((d) => d.focus_sec > 0).length;
  const best = f.days.reduce((a, d) => (d.longest_sec > (a?.longest_sec || 0) ? d : a), null);
  $("focus-stats").replaceChildren(
    stat("正事", dur(sum("focus_sec")), `${worked} 天有记录`),
    stat("日均", dur(worked ? sum("focus_sec") / worked : 0), "按有记录的天算"),
    stat("最长连续", dur(best?.longest_sec || 0), best ? `${best.date.slice(5)} ${clock(best.longest_start)} 开始` : "—"),
    stat("被打断", `${sum("interruptions")} 次`, `摸鱼 ${dur(sum("slack_sec"))}`),
  );
  const max = Math.max(1, ...f.days.map((d) => d.focus_sec));
  $("focus-cols").style.setProperty("--hue", hue("research"));
  $("focus-cols").replaceChildren(...f.days.map((d) => h("div", {
    class: "col",
    "data-tip": `${d.date} · 正事 ${dur(d.focus_sec)} · 最长连续 ${dur(d.longest_sec)} · 打断 ${d.interruptions} 次`,
  }, h("i", { style: { height: `${(d.focus_sec / max) * 100}%` } }), h("span", {}, d.date.slice(5).replace("-", "/")))));
  rankList($("channels"), f.channels.map((c) => ({ label: c.title, sub: names[c.category], value: c.seconds, hue: hue(c.category) })));
}

function stat(label, value, sub) {
  return h("div", { class: "stat" }, h("small", {}, label), h("b", {}, value), h("span", {}, sub));
}

async function loadSummary() {
  const during = $("during").value;
  const s = await priv(`/summary?${qs({ group, ...(during ? { during } : {}) })}`);
  const fmtSub = (i) => {
    if (group === "category") return i.line === "anchor" ? "锚定" : null;
    if (group === "site") return `${i.key.replace(/^www\./, "")} · ${i.visits} 次`;
    if (i.plays != null) return [i.artist, `${i.plays} 次`].filter(Boolean).join(" · ");
    return null;
  };
  rankList($("summary"), s.items.slice(0, 30).map((i) => ({
    label: i.title || i.key, sub: fmtSub(i), value: i.seconds, hue: hue(group === "category" ? i.key : GROUP_HUE[group]),
  })));
}

function setExport() {
  $("export-csv").href = `${API}/private/export?${qs({ format: "csv" })}`;
  $("export-json").href = `${API}/private/export?${qs({ format: "json" })}`;
}

async function setRange(from, to) {
  if (from > to) [from, to] = [to, from];
  range = { from, to };
  $("from").value = from;
  $("to").value = to;
  for (const b of $("presets").querySelectorAll("button")) {
    b.setAttribute("aria-pressed", String(to === today && addDays(today, 1 - Number(b.dataset.days)) === from));
  }
  setExport();
  await Promise.all([loadFocus(), loadSummary()]);
}

// -- 事件

const guard = (fn) => async (...args) => {
  try {
    await fn(...args);
  } catch (err) {
    showGate(err);
  }
};

$("prev").addEventListener("click", guard(() => loadDay(addDays($("date").value, -1))));
$("next").addEventListener("click", guard(() => loadDay(addDays($("date").value, 1))));
$("date").addEventListener("change", guard(() => $("date").value && loadDay($("date").value)));
$("from").addEventListener("change", guard(() => $("from").value && setRange($("from").value, range.to)));
$("to").addEventListener("change", guard(() => $("to").value && setRange(range.from, $("to").value)));
for (const b of $("presets").querySelectorAll("button")) {
  b.addEventListener("click", guard(() => setRange(addDays(today, 1 - Number(b.dataset.days)), today)));
}
for (const b of $("groups").querySelectorAll("button")) {
  b.addEventListener("click", guard(async () => {
    group = b.dataset.group;
    for (const x of $("groups").querySelectorAll("button")) x.setAttribute("aria-pressed", String(x === b));
    await loadSummary();
  }));
}
$("during").addEventListener("change", guard(loadSummary));

enableTips();
try {
  const d = await loadDay();
  today = d.date;
  $("date").max = today;
  $("next").disabled = true;
  $("gate").hidden = true;
  $("app").hidden = false;
  await setRange(addDays(today, -6), today);
} catch (err) {
  showGate(err);
}
