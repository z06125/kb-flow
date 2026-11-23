# kb-flow · 信息流 → 个人知识库 全自动流水线

> 把 RSS / 网页剪藏 / Telegram 收藏 / 本地文档，自动变成**带「知识卡」的可检索个人知识库**。
> GLM 驱动 · 一台 2 核 2G 云服务器即可跑 · Docker Compose 一键部署。

## 它解决什么问题

信息看了就忘、收藏夹吃灰。kb-flow 在你入库的瞬间用 GLM 生成**知识卡**
（一句话结论 + 要点 + 金句 + 标签 + 深挖问题），并向量化入库，
之后可以全文+语义混合检索、按日生成日报、通过 Telegram 随手收集。

## 架构

```
浏览器(篡改猴 kb-clipper) ─┐
RSS 订阅源每小时抓取 ──────┼─▶  kb-core (FastAPI + SQLite)
Telegram 收藏箱 Bot ───────┤      ├─ 知识卡流水线 (GLM-5.3-flash)
本地文档/电子书批量导入 ────┘      ├─ FTS5 中文全文 + 向量混合检索
                                  └─ Web UI (单文件, 深色)
ai CLI (sum/trans/prompt) ──▶ BigModel API        worker: 后台逐条生成知识卡
```

## 10 分钟部署

```bash
git clone <repo> kb && cd kb
cp .env.example .env && vi .env          # 填 BIGMODEL_API_KEY（智谱开放平台获取）
mkdir -p data
docker compose up -d --build
docker compose exec kb cat /data/.token  # 拿到访问令牌
# 浏览器打开 http://<服务器IP>:8000 ，输入令牌
```

国内服务器无法拉取 Docker Hub 时，先配置镜像加速（见 `docs/DEPLOY.md`）。

## 组件一览

| 路径 | 说明 |
|------|------|
| `server/app.py` | 知识库核心：入库/检索/日报/统计 API + Web UI |
| `server/worker.py` | 后台知识卡流水线（摘要→标签→向量） |
| `server/scripts/fetch_feeds.py` | RSS/Atom 采集器（trafilatura 正文抽取） |
| `server/scripts/tg_bot.py` | Telegram 收藏箱 Bot |
| `server/scripts/ingest_docs.py` | 本地文档/电子书(pdf/epub/txt/md)批量入库 |
| `userscripts/kb-clipper.user.js` | 篡改猴剪藏脚本：选中文字 Ctrl+Shift+K 一键入库 |
| `tools/ai.py` | 零依赖 CLI：长文摘要/翻译/提示词库/检索/日报 |
| `cf-worker/tg-relay.js` | Telegram API 反代（大陆服务器可用） |

## API 速览

```
POST /v1/clip        {text, url?, title?, source?, sync?}   # 剪藏入库
GET  /v1/search?q=                                           # 混合检索
GET  /v1/items?status=&source=&limit=                        # 列表
GET  /v1/digest?date=YYYY-MM-DD                              # 日报
GET  /v1/stats                                               # 库存与token消耗
```

鉴权：请求头 `X-API-Token: <令牌>`。设计为**只服务一个主人**（个人知识库），
请勿将 8000 端口对公网开放后共享令牌。

## Telegram 收藏箱

1. @BotFather 建 bot，token 写入 `.env` 的 `TG_BOT_TOKEN`
2. 大陆服务器加一行 `TG_API_BASE`（用 `cf-worker/tg-relay.js` 2 分钟部署反代）
3. 给 bot 发 `/start` 绑定，之后**转发任何消息/链接即入库**

## English (brief)

kb-flow turns RSS feeds, web clips, Telegram forwards and local documents into
a searchable personal knowledge base. Every item is summarized by GLM into a
"knowledge card" (TL;DR, key points, quotes, tags, follow-up questions) and
embedded for hybrid full-text + vector search. Single-file FastAPI app + SQLite,
runs comfortably on a 2-core/2GB VPS via Docker Compose. See deploy guide above.

## License

MIT
