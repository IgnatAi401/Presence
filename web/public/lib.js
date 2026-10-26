// 各页面共用：接口地址、建 DOM 的小工具、时长和时间的格式

const params = new URLSearchParams(location.search);
export const LOCAL = ["localhost", "127.0.0.1"].includes(location.hostname);
export const PROD_API = "https://api.ignat.ai/presence/api";
export const API = params.get("api") || (LOCAL ? "http://127.0.0.1:8790/presence/api" : PROD_API);
// 服务器给的封面地址写的是正式域名；本地调试时改成实际连接的接口
export const mediaUrl = (url) => (url.startsWith(`${PROD_API}/`) ? API + url.slice(PROD_API.length) : url);

export const $ = (id) => document.getElementById(id);
export const hue = (category) => `var(--c-${category}, var(--amber))`;

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "style") Object.assign(el.style, v);
    else if (k.startsWith("--")) el.style.setProperty(k, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) el.append(c);
  return el;
}

// 把 parent 的子节点原地改成 next 的样子：结构相同的元素保留，只改属性和文字。
// 每秒重画时卡片元素不被替换，鼠标泛光（data-lit、--spot-x/y）才不会一闪一闪
const KEEP = ["--spot-x", "--spot-y"];
function patchNode(a, b) {
  if (a === b) return a;
  if (a.nodeType !== b.nodeType || a.nodeName !== b.nodeName) {
    a.replaceWith(b);
    return b;
  }
  if (a.nodeType === Node.TEXT_NODE) {
    if (a.data !== b.data) a.data = b.data;
    return a;
  }
  const kept = KEEP.map((k) => a.style.getPropertyValue(k));
  for (const { name } of [...a.attributes]) if (name !== "data-lit" && !b.hasAttribute(name)) a.removeAttribute(name);
  for (const { name, value } of [...b.attributes]) if (a.getAttribute(name) !== value) a.setAttribute(name, value);
  KEEP.forEach((k, i) => { if (kept[i]) a.style.setProperty(k, kept[i]); });
  patch(a, [...b.childNodes]);
  return a;
}
export function patch(parent, next) {
  const old = [...parent.childNodes];
  next = next.filter((n) => n != null).map((n) => (typeof n === "string" ? document.createTextNode(n) : n));
  next.forEach((n, i) => {
    if (i < old.length) patchNode(old[i], n);
    else parent.append(n);
  });
  old.slice(next.length).forEach((n) => n.remove());
}

export function dur(sec) {
  sec = Math.max(0, Math.floor(sec));
  const hh = Math.floor(sec / 3600);
  const mm = Math.floor((sec % 3600) / 60);
  if (hh) return `${hh}h${String(mm).padStart(2, "0")}m`;
  if (mm) return `${mm} 分钟`;
  return `${sec} 秒`;
}

export const clock = (ts) =>
  new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });

export async function getJSON(path, init = {}) {
  const r = await fetch(`${API}${path}`, { cache: "no-store", ...init });
  if (!r.ok) throw Object.assign(new Error(`${path} ${r.status}`), { status: r.status, body: await r.json().catch(() => null) });
  return r.json();
}

// 本地日期 YYYY-MM-DD（按浏览器时区；服务器按自己的时区和凌晨 4 点切分一天）
export const isoDate = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
export function addDays(iso, n) {
  const [y, m, d] = iso.split("-").map(Number);
  return isoDate(new Date(y, m - 1, d + n));
}

// 一天的时间线色块（公开页的“今日”和历史页共用）：blocks 带 start / end / category / label
export function timelineBar(bar, day, blocks, extra = (b) => b.label) {
  const span = day.end - day.start;
  bar.replaceChildren(...[...bar.querySelectorAll(".cursor")], ...blocks.map((b) => h("span", {
    class: "blk",
    title: `${clock(b.start)}–${clock(b.end)} ${extra(b)}`,
    "--hue": hue(b.category),
    style: { left: `${((b.start - day.start) / span) * 100}%`, width: `${((b.end - b.start) / span) * 100}%` },
  })));
}

// 各类别时长的小图例：totals = {anchor: {...}, activity: {...}}
export function totalsList(ul, totals, names) {
  const rows = [...Object.entries(totals.anchor), ...Object.entries(totals.activity)]
    .filter(([, sec]) => sec >= 60)
    .sort((a, b) => b[1] - a[1]);
  ul.replaceChildren(...rows.map(([cat, sec]) =>
    h("li", { "--hue": hue(cat) }, h("b", {}, names?.[cat] || cat), h("span", {}, dur(sec)))));
}

// 悬停提示：给元素加 data-tip，鼠标或手指放上去时在旁边显示
export function enableTips(root = document) {
  const tip = h("div", { class: "tip", role: "tooltip", hidden: "" });
  document.body.append(tip);
  const show = (ev) => {
    const el = ev.target.closest?.("[data-tip]");
    if (!el || !root.contains(el)) { tip.hidden = true; return; }
    tip.textContent = el.dataset.tip;
    tip.hidden = false;
    const r = el.getBoundingClientRect();
    const w = tip.offsetWidth;
    const x = Math.min(window.innerWidth - w - 8, Math.max(8, r.left + r.width / 2 - w / 2));
    const y = r.top - tip.offsetHeight - 8;
    tip.style.left = `${x}px`;
    tip.style.top = `${y < 8 ? r.bottom + 8 : y}px`;
  };
  document.addEventListener("pointerover", show);
  document.addEventListener("focusin", show);
  document.addEventListener("pointerout", (ev) => { if (!ev.relatedTarget?.closest?.("[data-tip]")) tip.hidden = true; });
  window.addEventListener("scroll", () => { tip.hidden = true; }, { passive: true });
}

// 横向条形排行：rows = [{label, sub?, value, hue?}]
export function rankList(ul, rows, fmt = dur) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  ul.replaceChildren(...rows.map((r, i) => h("li", { "--hue": r.hue || "var(--amber)", "data-tip": `${r.label}${r.sub ? ` · ${r.sub}` : ""} · ${fmt(r.value)}` },
    h("span", { class: "n" }, String(i + 1)),
    h("span", { class: "name" }, h("b", {}, r.label), r.sub ? h("small", {}, r.sub) : null),
    h("span", { class: "v" }, fmt(r.value)),
    h("i", { class: "fill", style: { width: `${(r.value / max) * 100}%` } }),
  )));
  if (!rows.length) ul.replaceChildren(h("li", { class: "empty" }, "暂无记录"));
}
