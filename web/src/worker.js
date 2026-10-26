// 只处理打开 /me 页面（见 wrangler.jsonc 的 run_worker_first），其余地址直接由静态文件响应。
// 没有 401.ignat.ai 的登录 Cookie 就在这里跳去登录，不先加载空壳页面；
// Cookie 是否有效仍由 api.ignat.ai 上的 ignat-auth 检查，过期的话 me.js 再跳一次

const LOGIN = "https://401.ignat.ai/";
const COOKIE = "__Secure-ignat401";

export default {
  async fetch(request, env) {
    // 本地 pnpm dev 没有登录 Cookie，照常打开（私有接口由本机服务器的 dev_private 放行）
    if (env.LOCAL_DEV) return env.ASSETS.fetch(request);
    const cookies = (request.headers.get("cookie") || "").split(/;\s*/);
    if (!cookies.some((c) => c.startsWith(`${COOKIE}=`))) {
      const back = `https://${new URL(request.url).host}/me/`;
      return new Response(null, {
        status: 302,
        headers: { location: `${LOGIN}?return=${encodeURIComponent(back)}`, "cache-control": "no-store" },
      });
    }
    return env.ASSETS.fetch(request);
  },
};
