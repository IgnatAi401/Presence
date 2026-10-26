// 全站背景：深色底上一张细网格，几道各类别颜色的霓虹光沿网格线跑，到路口时偶尔拐弯，像电路板里的信号。
// 不再按页面取一个统一主题色：每张卡片用自己的颜色（见 tintCards）。
// 画布不可用时退回 style.css 里的静态网格；减少动态效果时只画静止的一帧

const GRID = 56; // 网格间距（CSS 像素），和 style.css 的静态网格一致
const PALETTE = ["dev", "video", "research", "music", "class", "game", "phone", "eat"];
const MAX_PULSES = 7;

// CSS 颜色（可以是 var(...)）→ [r, g, b]，0–255；带透明度的颜色按透明度变暗
const probe = document.createElement("i");
const rgbCache = new Map();
function rgb(css) {
  if (rgbCache.has(css)) return rgbCache.get(css);
  probe.style.color = "";
  probe.style.color = css;
  if (!probe.isConnected) { probe.hidden = true; document.body.append(probe); }
  const m = getComputedStyle(probe).color.match(/[\d.]+/g);
  const a = m && m[3] != null ? Number(m[3]) : 1;
  const out = m ? m.slice(0, 3).map((v) => Number(v) * a) : [255, 179, 107];
  rgbCache.set(css, out);
  return out;
}
const chroma = (c) => (Math.max(...c) - Math.min(...c)) / 255;

let live = 1; // 离线时不再发出新的光
export function setBgLive(on) { live = on ? 1 : 0; }

function start() {
  const canvas = document.createElement("canvas");
  canvas.className = "bg";
  canvas.setAttribute("aria-hidden", "true");
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  document.body.prepend(canvas);
  document.documentElement.classList.add("has-bg");

  const colors = PALETTE.map((c) => rgb(`var(--c-${c})`));
  const grid = document.createElement("canvas"); // 网格只在尺寸变化时重画
  let w = 0;
  let hgt = 0;
  let dpr = 1;
  const resize = () => {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = window.innerWidth;
    hgt = window.innerHeight;
    for (const c of [canvas, grid]) {
      c.width = Math.round(w * dpr);
      c.height = Math.round(hgt * dpr);
    }
    const g = grid.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = "#07070C";
    g.fillRect(0, 0, w, hgt);
    g.strokeStyle = "rgba(236, 234, 245, 0.035)";
    g.lineWidth = 1;
    g.beginPath();
    for (let x = 0.5; x < w; x += GRID) { g.moveTo(x, 0); g.lineTo(x, hgt); }
    for (let y = 0.5; y < hgt; y += GRID) { g.moveTo(0, y); g.lineTo(w, y); }
    g.stroke();
    // 路口的小点
    g.fillStyle = "rgba(236, 234, 245, 0.09)";
    for (let x = 0.5; x < w; x += GRID) for (let y = 0.5; y < hgt; y += GRID) g.fillRect(x - 0.5, y - 0.5, 1.5, 1.5);
    // 四角压暗，中间内容区更干净
    const v = g.createRadialGradient(w / 2, hgt * 0.4, Math.min(w, hgt) * 0.2, w / 2, hgt / 2, Math.max(w, hgt) * 0.75);
    v.addColorStop(0, "rgba(7, 7, 12, 0)");
    v.addColorStop(1, "rgba(7, 7, 12, 0.7)");
    g.fillStyle = v;
    g.fillRect(0, 0, w, hgt);
  };
  resize();
  window.addEventListener("resize", resize);

  // 一道光：沿网格线走，pts 是走过的拐点（最后一个是光头）
  const pulses = [];
  const rand = (a, b) => a + Math.random() * (b - a);
  const spawn = () => {
    const horizontal = Math.random() < 0.5;
    const lines = Math.floor((horizontal ? hgt : w) / GRID);
    const lane = (1 + Math.floor(Math.random() * Math.max(1, lines - 1))) * GRID + 0.5;
    const sign = Math.random() < 0.5 ? 1 : -1;
    const from = sign > 0 ? -40 : (horizontal ? w : hgt) + 40;
    const head = horizontal ? { x: from, y: lane } : { x: lane, y: from };
    pulses.push({
      pts: [{ ...head }, head],
      dir: horizontal ? { x: sign, y: 0 } : { x: 0, y: sign },
      speed: rand(70, 170),
      len: rand(120, 260),
      color: colors[Math.floor(Math.random() * colors.length)],
      done: 0,
    });
  };

  const step = (p, dt) => {
    const head = p.pts[p.pts.length - 1];
    let left = p.speed * dt;
    while (left > 0) {
      // 到下一个路口的距离
      const along = p.dir.x ? head.x : head.y;
      const next = p.dir.x + p.dir.y > 0 ? Math.floor((along - 0.5) / GRID + 1) * GRID + 0.5 : Math.ceil((along - 0.5) / GRID - 1) * GRID + 0.5;
      const gap = Math.abs(next - along);
      if (gap > left) { head.x += p.dir.x * left; head.y += p.dir.y * left; break; }
      head.x += p.dir.x * gap;
      head.y += p.dir.y * gap;
      left -= gap;
      const inside = head.x > 0 && head.x < w && head.y > 0 && head.y < hgt;
      if (inside && Math.random() < 0.16) {
        const turn = Math.random() < 0.5 ? 1 : -1;
        p.dir = { x: -p.dir.y * turn, y: p.dir.x * turn };
        p.pts[p.pts.length - 1] = { x: head.x, y: head.y };
        p.pts.push({ x: head.x, y: head.y });
        return step(p, left / p.speed);
      }
    }
    // 只留尾巴长度以内的拐点
    let total = 0;
    for (let i = p.pts.length - 1; i > 0; i--) {
      total += Math.hypot(p.pts[i].x - p.pts[i - 1].x, p.pts[i].y - p.pts[i - 1].y);
      if (total > p.len && i > 1) { p.pts.splice(0, i - 1); break; }
    }
    const margin = p.len + 40;
    if (head.x < -margin || head.x > w + margin || head.y < -margin || head.y > hgt + margin) p.done = 1;
  };

  // 从光头往回画，越往后越淡：一层宽的晕、一层细的亮线，光头一个亮点
  const drawPulse = (p) => {
    const [r, g, b] = p.color;
    let dist = 0;
    for (let i = p.pts.length - 1; i > 0 && dist < p.len; i--) {
      const a = p.pts[i];
      const z = p.pts[i - 1];
      const seg = Math.hypot(a.x - z.x, a.y - z.y);
      if (!seg) continue;
      const use = Math.min(seg, p.len - dist);
      const ex = a.x + ((z.x - a.x) / seg) * use;
      const ey = a.y + ((z.y - a.y) / seg) * use;
      const a0 = 1 - dist / p.len;
      const a1 = 1 - (dist + use) / p.len;
      const grad = ctx.createLinearGradient(a.x, a.y, ex, ey);
      grad.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${a0})`);
      grad.addColorStop(1, `rgba(${r}, ${g}, ${b}, ${a1})`);
      ctx.strokeStyle = grad;
      ctx.globalAlpha = 0.16;
      ctx.lineWidth = 7;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(ex, ey); ctx.stroke();
      ctx.globalAlpha = 0.85;
      ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(ex, ey); ctx.stroke();
      dist += use;
    }
    const head = p.pts[p.pts.length - 1];
    ctx.globalAlpha = 1;
    const glow = ctx.createRadialGradient(head.x, head.y, 0, head.x, head.y, 14);
    glow.addColorStop(0, `rgba(${r}, ${g}, ${b}, 0.55)`);
    glow.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
    ctx.fillStyle = glow;
    ctx.fillRect(head.x - 14, head.y - 14, 28, 28);
    ctx.fillStyle = "rgba(255, 255, 255, 0.9)";
    ctx.fillRect(head.x - 1, head.y - 1, 2, 2);
  };

  const paint = () => {
    if (!grid.width || !grid.height) return; // 窗口还没尺寸（后台标签页）
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    ctx.drawImage(grid, 0, 0);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalCompositeOperation = "lighter";
    ctx.lineCap = "round";
    for (const p of pulses) drawPulse(p);
  };

  const still = window.matchMedia("(prefers-reduced-motion: reduce)");
  // 静止画面：随便摆几道光
  const freeze = () => {
    pulses.length = 0;
    for (let i = 0; i < 5; i++) {
      spawn();
      const p = pulses[i];
      for (let k = 0; k < 40; k++) step(p, rand(0.05, 0.12));
      if (p.done) pulses.pop();
    }
    paint();
  };

  let last = 0;
  let prev = 0;
  let nextSpawn = 0;
  const loop = (now) => {
    requestAnimationFrame(loop);
    if (still.matches || document.hidden || now - last < 33) return; // 约 30 帧
    last = now;
    const dt = Math.min(0.1, (now - (prev || now)) / 1000);
    prev = now;
    if (live && now > nextSpawn && pulses.length < MAX_PULSES) {
      spawn();
      nextSpawn = now + rand(500, 1600);
    }
    for (const p of pulses) step(p, dt);
    for (let i = pulses.length - 1; i >= 0; i--) if (pulses[i].done) pulses.splice(i, 1);
    paint();
  };
  // 一打开就有几道光在半路，不从空白开始
  for (let i = 0; i < 4; i++) {
    spawn();
    for (let k = 0; k < 30 + i * 25; k++) step(pulses[i], 0.05);
  }
  if (still.matches) freeze();
  else paint();
  requestAnimationFrame(loop);
  still.addEventListener?.("change", () => { if (still.matches) freeze(); });
  window.addEventListener("resize", () => { if (still.matches) freeze(); });
  document.addEventListener("visibilitychange", () => { prev = 0; });
}

// 每张卡片一个主题色：自己没写 --hue 的卡片（时间线、历史面板），
// 按卡片里有颜色的东西（时间线色块、图例、排行条、柱子）的面积取最多的那种颜色；
// 灰色（离开、离线）不算。什么颜色都没有时按卡片顺序轮流用调色板里的颜色
const AUTO = ".today, .panel";
function tintCards() {
  document.querySelectorAll(AUTO).forEach((card, index) => {
    if (card.style.getPropertyValue("--hue") && !card.hasAttribute("data-auto-hue")) return;
    const score = new Map();
    for (const el of card.querySelectorAll('[style*="--hue"]')) {
      const css = el.style.getPropertyValue("--hue").trim();
      if (!css || chroma(rgb(css)) < 0.15) continue;
      const r = el.getBoundingClientRect();
      const area = r.width * r.height;
      if (area > 0) score.set(css, (score.get(css) || 0) + area);
    }
    const best = [...score].sort((a, b) => b[1] - a[1])[0]?.[0] || `var(--c-${PALETTE[index % PALETTE.length]})`;
    card.setAttribute("data-auto-hue", "");
    if (card.style.getPropertyValue("--hue").trim() !== best) card.style.setProperty("--hue", best);
  });
}
let tintTimer = 0;
const queueTint = () => {
  if (!tintTimer) tintTimer = setTimeout(() => { tintTimer = 0; tintCards(); }, 200);
};

// 鼠标泛光（参照 personal_profile 的 lib/motion.ts）：只点亮鼠标所在的那张卡片（data-lit），
// 光斑坐标相对卡片自身（--spot-x / --spot-y），每帧最多算一次；触屏和减少动态效果时不做
const LIT = ".hero, .today, .tile > .in, .tile.big .card, .hero-card .card:not(.web), .panel, .stat";

const fine = window.matchMedia("(hover: hover) and (pointer: fine)");
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
let px = 0;
let py = 0;
let under = null;
let lit = null;
let pending = 0;
function light() {
  pending = 0;
  const el = under && fine.matches && !reduced.matches ? under.closest(LIT) : null;
  if (el !== lit) {
    lit?.removeAttribute("data-lit");
    lit?.style.removeProperty("--tilt-x");
    lit?.style.removeProperty("--tilt-y");
    lit = el;
    lit?.setAttribute("data-lit", "");
  }
  if (!el) return;
  const r = el.getBoundingClientRect();
  el.style.setProperty("--spot-x", `${(px - r.left).toFixed(1)}px`);
  el.style.setProperty("--spot-y", `${(py - r.top).toFixed(1)}px`);
  // 悬停倾斜（见 style.css）：鼠标在卡片里的位置换算成 -1…1
  const tx = Math.min(1, Math.max(0, (px - r.left) / r.width));
  const ty = Math.min(1, Math.max(0, (py - r.top) / r.height));
  el.style.setProperty("--tilt-x", ((0.5 - ty) * 2).toFixed(3));
  el.style.setProperty("--tilt-y", ((tx - 0.5) * 2).toFixed(3));
}
const schedule = () => { if (!pending) pending = requestAnimationFrame(light); };
document.addEventListener("pointermove", (ev) => {
  if (ev.pointerType !== "mouse") return;
  px = ev.clientX;
  py = ev.clientY;
  under = ev.target instanceof Element ? ev.target : null;
  schedule();
}, { passive: true });
// 滚动时鼠标下面的内容变了
window.addEventListener("scroll", () => {
  if (!lit) return;
  under = document.elementFromPoint(px, py);
  schedule();
}, { passive: true });
const leave = () => { under = null; schedule(); };
document.documentElement.addEventListener("pointerleave", leave);
window.addEventListener("blur", leave);

function boot() {
  start();
  tintCards();
  new MutationObserver(queueTint).observe(document.body, { childList: true, subtree: true });
  window.addEventListener("resize", queueTint);
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
else boot();
