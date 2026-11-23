// Cloudflare Worker：Telegram Bot API 反向代理（免费额度足够个人长期使用）
//
// 部署（2 分钟）：
//   1. dash.cloudflare.com 注册/登录 → Workers & Pages → Create Worker
//   2. 全选示例代码删除，粘贴本文件 → Deploy
//   3. 服务器 .env 增加：TG_API_BASE=https://<worker名>.<你的子域>.workers.dev
//   4. docker compose restart bot
//
// 注意：*.workers.dev 在大陆部分网络不稳定；如你有自己的域名，
// 在 Worker 的 Settings → Triggers 里绑定自定义域更稳。

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const upstream = await fetch("https://api.telegram.org" + url.pathname + url.search, {
      method: request.method,
      headers: request.headers,
      body: request.body,
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("Content-Type") || "application/json",
      },
    });
  },
};
