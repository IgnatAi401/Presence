// now.ignat.ai/history 公开历史（首页的一个视图，见 views.js）：正事热力图、某一天的时间线、按周 / 月的趋势、游戏和听歌榜

import { $, addDays, dur, enableTips, getJSON, h, hue, rankList, timelineBar, totalsList } from "/lib.js";

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];
// 热力图分档（小时）：0 单独一档，其余越深越多
const LEVELS = [0, 1, 2, 4, 6];

let hist = null;
let unit = "week";
let picked = null; // 趋势图选中的类别

const level = (sec) => (sec <= 0 ? 0 : LEVELS.filter((x) => sec / 3600 >= x).length);
const weekday = (iso) => (new Date(`${iso}T12:00:00`).getDay() + 6) % 7; // 周一为 0

function renderHeat() {
  const days = hist.days;
  const pad = weekday(days[0].date);
  const cells = [...Array(pad).fill(null), ...days];
  const heat = $("heat");
  heat.style.setProperty("--weeks", Math.ceil(cells.length / 7));
  heat.replaceChildren(
    ...WEEKDAYS.map((w, i) => h("span", { class: "wd", style: { gridRow: i + 1 } }, i % 2 ? "" : w)),
    ...cells.map((d) => d
      ? h("button", {
        type: "button", class: "cell", "data-level": level(d.serious_sec), "data-date": d.date,
        "data-tip": `${d.date} 周${WEEKDAYS[weekday(d.date)]} · 正事 ${dur(d.serious_sec)}`,
        "aria-label": `${d.date} 正事 ${dur(d.serious_sec)}`,
        onclick: () => loadDay(d.date),
      })
      : h("span", { class: "cell blank" })),
  );
  const total = days.reduce((s, d) => s + d.serious_sec, 0);
  const active = days.filter((d) => d.serious_sec > 0).length;
  $("heat-sum").textContent = `${days.length} 天里有 ${active} 天 · 共 ${dur(total)}`;
  $("heat-legend").replaceChildren(h("span", {}, "少"),
    ...[0, 1, 2, 3, 4, 5].map((l) => h("i", { class: "cell", "data-level": l, "data-tip": l ? `≥ ${LEVELS[l - 1] || "0"} 小时` : "没有" })),
    h("span", {}, "多"));
  scrollHeat();
}

// 窄屏先看最近几周；视图隐藏时量不出宽度，切过来时再滚
function scrollHeat() {
  const heat = $("heat");
  heat.parentElement.scrollLeft = heat.scrollWidth;
}
addEventListener("viewchange", (ev) => { if (ev.detail === "history" && hist) scrollHeat(); });

function markPicked(date) {
  for (const c of $("heat").querySelectorAll(".cell[aria-current]")) c.removeAttribute("aria-current");
  $("heat").querySelector(`.cell[data-date="${date}"]`)?.setAttribute("aria-current", "date");
}

async function loadDay(date) {
  $("date").value = date;
  markPicked(date);
  const last = hist.days.at(-1).date;
  $("next").disabled = date >= last;
  try {
    const day = await getJSON(`/day?date=${date}`);
    timelineBar($("day-bar"), day, day.timeline);
    totalsList($("day-totals"), day.totals, day.names);
    if (!day.timeline.length) $("day-totals").replaceChildren(h("li", { class: "empty" }, "这天没有记录"));
  } catch {
    $("history-notice").hidden = false;
  }
}

// 每个周期（周一开始的周 / 自然月）各类别的时长
function periods() {
  const map = new Map();
  for (const d of hist.days) {
    const key = unit === "week" ? addDays(d.date, -weekday(d.date)) : d.date.slice(0, 7);
    const p = map.get(key) || { key, cats: {} };
    for (const line of Object.values(d.totals)) {
      for (const [c, s] of Object.entries(line)) p.cats[c] = (p.cats[c] || 0) + s;
    }
    map.set(key, p);
  }
  return [...map.values()];
}

function renderTrend() {
  const ps = periods();
  const sums = {};
  for (const p of ps) for (const [c, s] of Object.entries(p.cats)) sums[c] = (sums[c] || 0) + s;
  const cats = Object.keys(sums).filter((c) => sums[c] >= 600).sort((a, b) => sums[b] - sums[a]);
  if (!cats.includes(picked)) picked = cats.includes("research") ? "research" : cats[0];
  $("chips").replaceChildren(...cats.map((c) => h("button", {
    type: "button", "aria-pressed": String(c === picked), "--dot": hue(c),
    onclick: () => { picked = c; renderTrend(); },
  }, hist.names[c] || c)));
  const max = Math.max(1, ...ps.map((p) => p.cats[picked] || 0));
  const name = hist.names[picked] || picked;
  $("trend").replaceChildren(...ps.map((p) => {
    const sec = p.cats[picked] || 0;
    const label = unit === "week" ? `${p.key.slice(5)} 起` : p.key;
    return h("div", { class: "col", "data-tip": `${label} · ${name} ${dur(sec)}` },
      h("i", { style: { height: `${(sec / max) * 100}%` } }),
      h("span", {}, unit === "week" ? p.key.slice(5).replace("-", "/") : `${Number(p.key.slice(5))}月`));
  }));
  if (!cats.length) $("trend").replaceChildren(h("p", { class: "empty" }, "还没有足够的记录"));
}

function renderRanks() {
  rankList($("games"), hist.games.map((g) => ({ label: g.title || g.key, sub: `${g.plays} 次`, value: g.seconds, hue: "inherit" })));
  rankList($("music"), hist.music.map((m) => ({ label: m.title || m.key, sub: [m.artist, `${m.plays} 次`].filter(Boolean).join(" · "),
    value: m.seconds, hue: "inherit" })));
}

for (const b of document.querySelectorAll(".seg button")) {
  b.addEventListener("click", () => {
    unit = b.dataset.unit;
    for (const x of document.querySelectorAll(".seg button")) x.setAttribute("aria-pressed", String(x === b));
    renderTrend();
  });
}
$("prev").addEventListener("click", () => loadDay(addDays($("date").value, -1)));
$("next").addEventListener("click", () => loadDay(addDays($("date").value, 1)));
$("date").addEventListener("change", () => $("date").value && loadDay($("date").value));

enableTips();
try {
  hist = await getJSON("/history");
  const last = hist.days.at(-1).date;
  $("date").max = last;
  $("date").min = hist.days[0].date;
  renderHeat();
  renderTrend();
  renderRanks();
  await loadDay(new URLSearchParams(location.search).get("date") || last);
} catch {
  $("history-notice").hidden = false;
}
