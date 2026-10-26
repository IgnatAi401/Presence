// 此刻 / 历史是同一个页面的两个视图：点标签只换视图、改地址，不重新加载，背景动画和实时连接都不中断。
// 支持的浏览器用 View Transitions 做淡入滑动；历史的脚本和数据第一次切过去时才加载

const ORDER = ["now", "history"];
const TITLES = { now: "Now · Ignat", history: "History · Ignat" };
const root = document.documentElement;
const viewOf = (path) => (/^\/history\/?$/.test(path) ? "history" : "now");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const scrolls = {}; // 每个视图各记各的滚动位置

let historyMod = null;
let wanted = null; // 最近一次点的视图
const loadHistory = () => (historyMod ||= import("/history.js").catch(() => {}));

function markTab(view) {
  for (const a of document.querySelectorAll(".tabs a[data-view]")) {
    if (a.dataset.view === view) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
}

function apply(view) {
  const prev = root.dataset.view;
  if (prev !== view) scrolls[prev] = scrollY;
  root.dataset.view = view;
  markTab(view);
  document.title = TITLES[view]; // 此刻视图每秒会换成当前状态
  scrollTo(0, scrolls[view] || 0);
  window.dispatchEvent(new CustomEvent("viewchange", { detail: view }));
}

async function show(view) {
  wanted = view;
  markTab(view); // 标签先亮，等数据时也有反馈
  if (view === root.dataset.view) return;
  root.dataset.dir = ORDER.indexOf(view) > ORDER.indexOf(root.dataset.view) ? "fwd" : "back";
  // 第一次打开历史要拉数据：在过渡开始前等（最多 300ms），等不到就先切过去，数据到了再填进去。
  // 不能放进 startViewTransition 的回调里等：回调期间浏览器冻结画面，看着就是卡住
  if (view === "history") {
    await Promise.race([loadHistory(), sleep(300)]);
    if (wanted !== view) return; // 等的时候又点了别的
  }
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return apply(view);
  if (!document.startViewTransition) {
    // 不支持 View Transitions 的浏览器：新视图淡入
    apply(view);
    document.getElementById(`view-${view}`).animate({ opacity: [0, 1] }, { duration: 250, easing: "ease-out" });
    return;
  }
  document.startViewTransition(() => apply(view));
}

for (const a of document.querySelectorAll(".tabs a[data-view]")) {
  a.addEventListener("click", (ev) => {
    // 新标签页打开等照常走浏览器
    if (ev.button || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    ev.preventDefault();
    if (a.dataset.view === (wanted || root.dataset.view)) return;
    history.pushState(null, "", a.href);
    show(a.dataset.view);
  });
}
// 鼠标移上去、按下去就开始拉历史，点下时多半已经好了
const tab = document.querySelector('.tabs a[data-view="history"]');
for (const type of ["pointerenter", "pointerdown", "focus"]) tab.addEventListener(type, loadHistory, { once: true });

history.scrollRestoration = "manual";
addEventListener("popstate", () => show(viewOf(location.pathname)));

apply(viewOf(location.pathname));
if (root.dataset.view === "history") loadHistory();
