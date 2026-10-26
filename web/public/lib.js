// 各页面共用：接口地址、建 DOM 的小工具、时长和时间的格式

const params = new URLSearchParams(location.search);
export const LOCAL = ["localhost", "127.0.0.1"].includes(location.hostname);
export const PROD_API = "https://api.ignat.ai/presence/api";
export const API = params.get("api") || (LOCAL ? "http://127.0.0.1:8790/presence/api" : PROD_API);
// 服务器给的封面地址写的是正式域名；本地调试时改成实际连接的接口
export const mediaUrl = (url) => (url.startsWith(`${PROD_API}/`) ? API + url.slice(PROD_API.length) : url);

export const $ = (id) => document.getElementById(id);
export const hue = (category) => `var(--c-${category}, var(--amber))`;

// 带图片的卡片（封面、App 图标）用图片的主色当主题色：缩成 24×24，按色相分 12 组，
// 越鲜艳的像素权重越大，取权重最大的一组的平均色，再提亮成霓虹色。
// 第一次调用时返回 null 并开始加载，加载完调用 onReady（各页每秒重画，下次就用上了）；
// 几乎没有颜色（黑白封面、单色图标）时用白色；图片读不了（跨域没给权限）时一直是 null，卡片退回类别颜色
const imageHues = new Map();
export function imageHue(url, onReady) {
  if (!url) return null;
  if (imageHues.has(url)) return imageHues.get(url);
  imageHues.set(url, null);
  if (imageHues.size > 200) imageHues.delete(imageHues.keys().next().value);
  const img = new Image();
  img.crossOrigin = "anonymous";
  img.decoding = "async";
  img.onload = () => {
    try {
      const c = document.createElement("canvas");
      c.width = c.height = 24;
      const g = c.getContext("2d", { willReadFrequently: true });
      g.drawImage(img, 0, 0, 24, 24);
      const px = g.getImageData(0, 0, 24, 24).data;
      const bins = Array.from({ length: 12 }, () => ({ w: 0, r: 0, g: 0, b: 0 }));
      for (let i = 0; i < px.length; i += 4) {
        if (px[i + 3] < 128) continue;
        const [r, gg, b] = [px[i], px[i + 1], px[i + 2]];
        const max = Math.max(r, gg, b);
        const min = Math.min(r, gg, b);
        const sat = max ? (max - min) / max : 0;
        if (sat < 0.25 || max < 50) continue;
        let hh = max === r ? (gg - b) / (max - min) : max === gg ? 2 + (b - r) / (max - min) : 4 + (r - gg) / (max - min);
        hh = ((hh * 60) + 360) % 360;
        const bin = bins[Math.floor(hh / 30) % 12];
        const w = sat * sat * (max / 255);
        bin.w += w; bin.r += r * w; bin.g += gg * w; bin.b += b * w;
      }
      const best = bins.reduce((a, b) => (b.w > a.w ? b : a));
      let css = "#F2F0FA"; // 鲜艳的像素太少：白色
      if (best.w >= 8) {
        const [hh, s] = hsl(best.r / best.w, best.g / best.w, best.b / best.w);
        css = `hsl(${Math.round(hh)} ${Math.round(Math.max(s, 0.72) * 100)}% 64%)`;
      }
      imageHues.set(url, css);
      onReady?.(css);
    } catch {
      /* 跨域图片读不了像素 */
    }
  };
  img.src = url;
  return null;
}
function hsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  const d = max - min;
  if (!d) return [0, 0, l];
  const s = d / (1 - Math.abs(2 * l - 1));
  const hh = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [(hh * 60 + 360) % 360, s, l];
}

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

// 时间线会格式化几千次时间；复用格式器，避免每个色块都初始化区域设置。
const clockFormatter = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
export const clock = (ts) => clockFormatter.format(new Date(ts * 1000));

// 明细按批创建节点；保留全部记录，可继续加载，换日期会重置。
export function pagedList(ul, rows, renderRow, emptyText, pageSize = 100) {
  ul._moreButton?.remove();
  ul.replaceChildren();
  let shown = 0;
  const more = h("button", { type: "button", class: "btn log-more" });
  ul._moreButton = more;
  const append = () => {
    const end = Math.min(rows.length, shown + pageSize);
    ul.append(...rows.slice(shown, end).map(renderRow));
    shown = end;
    more.textContent = `再显示 ${Math.min(pageSize, rows.length - shown)} 条（已显示 ${shown} / ${rows.length}）`;
    more.hidden = shown >= rows.length;
  };
  more.addEventListener("click", append);
  ul.after(more);
  if (rows.length) append();
  else { ul.append(h("li", { class: "empty" }, emptyText)); more.hidden = true; }
}

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
