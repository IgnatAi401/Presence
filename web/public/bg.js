// 全站背景：WebGL 画缓慢流动的雾光。最多四个光源，颜色取自页面上当前看得见的主要几种颜色
// （带 --hue 的元素按可见面积加权）；主光源在左上方，还会一圈圈扩散微弱的“电波”。
// 主页用 setBgHue 指定主光源（当前状态的颜色），详细历史页固定绿色。
// 不支持 WebGL 时退回 CSS 渐变，减少动态效果时只画静止的一帧

const VERT = `
attribute vec2 a;
void main() { gl_Position = vec4(a, 0.0, 1.0); }
`;

const FRAG = `
// 必须用 highp：mediump 在苹果显卡上是 16 位浮点，开久了坐标变大，噪声会退化成满屏亮雾
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec2 uRes;
uniform float uTime;
uniform vec3 uC[4];
uniform float uW[4];
uniform float uLive;

// 小乘数的哈希，输入先按 289 取模，数值始终很小
float hash(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
float cell(vec2 i) { return hash(mod(i, 289.0)); }
float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(cell(i), cell(i + vec2(1.0, 0.0)), u.x),
             mix(cell(i + vec2(0.0, 1.0)), cell(i + vec2(1.0, 1.0)), u.x), u.y);
}
float fbm(vec2 p) {
  float v = 0.0;
  float a = 0.5;
  mat2 m = mat2(1.6, 1.2, -1.2, 1.6);
  for (int i = 0; i < 5; i++) {
    v += a * noise(p);
    p = m * p;
    a *= 0.5;
  }
  return v;
}

// 光源位置（窗口里的比例，左下为原点）：主光源左上，其余分在右侧、左下、右上
vec2 anchor(int i) {
  if (i == 0) return vec2(0.28, 0.8);
  if (i == 1) return vec2(0.9, 0.55);
  if (i == 2) return vec2(0.1, 0.18);
  return vec2(0.74, 0.06);
}

void main() {
  vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / uRes.y;
  float t = uTime * 0.025;

  // 两层域扭曲的噪声：像慢慢翻涌的烟和光
  vec2 q = vec2(fbm(p * 1.3 + vec2(0.0, t)), fbm(p * 1.3 + vec2(5.2, -t)));
  float n = fbm(p * 1.1 + 2.4 * q + vec2(t * 0.7, -t * 0.4));

  vec3 col = vec3(0.043, 0.035, 0.031);
  vec3 amber = vec3(1.0, 0.70, 0.42);

  // 各光源的光按距离衰减叠加；local 是这一点附近的主导颜色
  vec3 light = vec3(0.0);
  float sum = 0.0;
  float d0 = 0.0;
  for (int i = 0; i < 4; i++) {
    float fi = float(i);
    vec2 c = anchor(i) + 0.05 * vec2(sin(uTime * 0.05 + fi * 1.7), cos(uTime * 0.04 + fi * 2.3));
    vec2 cp = (c * uRes - 0.5 * uRes) / uRes.y;
    float d = length((p - cp) * vec2(0.8, 1.0));
    if (i == 0) d0 = d;
    float g = exp(-d * d * 1.5) * uW[i];
    light += uC[i] * g;
    sum += g;
  }
  vec3 local = light / max(sum, 0.001);
  float near = clamp(sum, 0.0, 1.2);

  // 雾：颜色跟着最近的光源走，夹一点琥珀色；离光源越近越亮
  float haze = smoothstep(0.45, 0.82, n);
  vec3 tint = mix(local, amber, smoothstep(0.35, 0.75, q.y) * 0.25);
  col += tint * haze * haze * (0.05 + 0.15 * near);
  col += light * 0.05;
  col += amber * pow(smoothstep(0.32, 0.62, q.x * n * 1.6), 2.0) * 0.03 * (1.0 - near);

  // 电波：从主光源向外扩散，越远越淡；离线时不画
  float wave = pow(0.5 + 0.5 * sin(d0 * 24.0 - uTime * 0.9 + n * 3.0), 36.0);
  col += uC[0] * wave * exp(-d0 * 1.7) * smoothstep(0.08, 0.35, d0) * (0.08 + 0.12 * haze) * uLive * uW[0];

  col *= 1.0 - 0.55 * smoothstep(0.35, 1.3, length(p * vec2(0.85, 1.1)));
  col += (hash(gl_FragCoord.xy + fract(uTime * 7.0) * 61.0) - 0.5) * 0.012; // 抖动，避免暗部色带
  gl_FragColor = vec4(col, 1.0);
}
`;

const N = 4;
const AMBER = [1, 0.7, 0.42];
// 每个光源：颜色和强度；画面里的值每帧向目标慢慢靠拢
const target = { colors: Array.from({ length: N }, () => AMBER.slice()), weights: [1, 0, 0, 0], live: 1 };
const cur = { colors: target.colors.map((c) => c.slice()), weights: target.weights.slice(), live: 1 };
let primary = null; // 指定的主光源颜色（CSS），没有就用页面上最多的颜色
let draw = null;

// CSS 颜色（可以是 var(...)）→ [r, g, b]，0–1；带透明度的颜色按透明度变暗
const probe = document.createElement("i");
const rgbCache = new Map();
function rgb(css) {
  if (rgbCache.has(css)) return rgbCache.get(css);
  probe.style.color = "";
  probe.style.color = css;
  if (!probe.isConnected) { probe.hidden = true; document.body.append(probe); }
  const m = getComputedStyle(probe).color.match(/[\d.]+/g);
  const a = m && m[3] != null ? Number(m[3]) : 1;
  const out = m ? m.slice(0, 3).map((v) => (Number(v) / 255) * a) : AMBER.slice();
  rgbCache.set(css, out);
  return out;
}
const chroma = (c) => Math.max(...c) - Math.min(...c);
const close = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) < 0.18;

// 看得见的带 --hue 元素按可见面积（单个最多算视口的 12%）给颜色加权，取最多的几种
function pageColors() {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const cap = vw * vh * 0.12;
  const score = new Map();
  for (const el of document.querySelectorAll('[style*="--hue"]')) {
    const css = el.style.getPropertyValue("--hue").trim();
    if (!css) continue;
    const r = el.getBoundingClientRect();
    const w = Math.min(r.right, vw) - Math.max(r.left, 0);
    const h = Math.min(r.bottom, vh) - Math.max(r.top, 0);
    if (w <= 0 || h <= 0) continue;
    score.set(css, (score.get(css) || 0) + Math.min(w * h, cap));
  }
  return [...score].sort((a, b) => b[1] - a[1]).map(([css, area]) => ({ css, c: rgb(css), area }));
}

function refresh() {
  const found = pageColors();
  const first = primary ? { css: primary, c: rgb(primary) } : found.find((f) => chroma(f.c) > 0.12) || { css: "var(--amber)", c: AMBER };
  // 其余光源：有颜色感（不是灰色）、和已选的不太像
  const picked = [first];
  for (const f of found) {
    if (picked.length >= N) break;
    if (chroma(f.c) > 0.12 && !picked.some((p) => close(p.c, f.c))) picked.push(f);
  }
  const top = Math.max(1, ...picked.slice(1).map((f) => f.area));
  picked.forEach((f, i) => {
    target.colors[i] = f.c.slice();
    target.weights[i] = i === 0 ? 1 : 0.45 + 0.4 * Math.sqrt(f.area / top);
  });
  for (let i = picked.length; i < N; i++) target.weights[i] = 0;
  // 顶部导航的高亮用主光源的颜色
  document.documentElement.style.setProperty("--glow", first.css);
  draw?.();
}
let refreshTimer = 0;
const queueRefresh = () => {
  if (!refreshTimer) refreshTimer = setTimeout(() => { refreshTimer = 0; refresh(); }, 250);
};

export function setBgHue(css, live = true) {
  primary = css;
  target.live = live ? 1 : 0;
  refresh();
}

function start() {
  const canvas = document.createElement("canvas");
  canvas.className = "bg";
  canvas.setAttribute("aria-hidden", "true");
  const gl = canvas.getContext("webgl", { antialias: false, alpha: false, powerPreference: "low-power" });
  if (!gl) return;

  const shader = (type, src) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null;
  };
  const vs = shader(gl.VERTEX_SHADER, VERT);
  const fs = shader(gl.FRAGMENT_SHADER, FRAG);
  if (!vs || !fs) return;
  const prog = gl.createProgram();
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return;
  gl.useProgram(prog);

  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, "a");
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  const u = Object.fromEntries(["uRes", "uTime", "uC", "uW", "uLive"].map((k) => [k, gl.getUniformLocation(prog, k)]));

  document.body.prepend(canvas);
  document.documentElement.classList.add("has-bg");

  // 画面很柔和，用一半分辨率就够，省电
  const resize = () => {
    const scale = Math.min(window.devicePixelRatio || 1, 2) * 0.5;
    canvas.width = Math.max(1, Math.round(window.innerWidth * scale));
    canvas.height = Math.max(1, Math.round(window.innerHeight * scale));
    gl.viewport(0, 0, canvas.width, canvas.height);
  };
  resize();
  window.addEventListener("resize", () => { resize(); queueRefresh(); });

  const still = window.matchMedia("(prefers-reduced-motion: reduce)");
  const t0 = performance.now();
  let last = 0;
  const frame = (now, settle) => {
    const k = settle ? 1 : 0.03;
    for (let i = 0; i < N; i++) {
      for (let j = 0; j < 3; j++) cur.colors[i][j] += (target.colors[i][j] - cur.colors[i][j]) * k;
      cur.weights[i] += (target.weights[i] - cur.weights[i]) * k;
    }
    cur.live += (target.live - cur.live) * k;
    gl.uniform2f(u.uRes, canvas.width, canvas.height);
    gl.uniform1f(u.uTime, still.matches ? 40 : (now - t0) / 1000);
    gl.uniform3fv(u.uC, cur.colors.flat());
    gl.uniform1fv(u.uW, cur.weights);
    gl.uniform1f(u.uLive, cur.live);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };
  draw = () => { if (still.matches) frame(performance.now(), true); };
  const loop = (now) => {
    requestAnimationFrame(loop);
    if (still.matches || now - last < 33) return; // 约 30 帧
    last = now;
    frame(now, false);
  };
  refresh();
  frame(performance.now(), true);
  requestAnimationFrame(loop);
  still.addEventListener?.("change", () => draw());
  // 页面内容变了、滚动到别处时重新挑颜色（主页每秒重画，但颜色没变时结果一样）
  window.addEventListener("scroll", queueRefresh, { passive: true });
  setInterval(refresh, 3000);
}

// 鼠标泛光（参照 personal_profile 的 lib/motion.ts）：只点亮鼠标所在的那张卡片（data-lit），
// 光斑坐标相对卡片自身（--spot-x / --spot-y），每帧最多算一次；触屏和减少动态效果时不做
const LIT = ".hero, .today, .tile > .in, .tile.big .card, .hero-card .card:not(.web), .panel";
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
    lit = el;
    lit?.setAttribute("data-lit", "");
  }
  if (!el) return;
  const r = el.getBoundingClientRect();
  el.style.setProperty("--spot-x", `${(px - r.left).toFixed(1)}px`);
  el.style.setProperty("--spot-y", `${(py - r.top).toFixed(1)}px`);
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

// 页面自带主题色（详细历史页是绿色）
const theme = document.documentElement.dataset.glow;

function boot() {
  start();
  if (theme) {
    setBgHue(`var(--c-${theme})`);
    cur.colors[0] = target.colors[0].slice(); // 一打开就是主题色，不从琥珀色渐变过去
  }
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
else boot();
