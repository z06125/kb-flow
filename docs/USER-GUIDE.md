# kb-flow 操作手册

> 面向唯一主人（单用户系统）。首次部署见 `docs/DEPLOY.md`，本文讲日常使用。

## 0. 每天 30 秒用法（最短路径）

```
看到好文章 → 选中文字 Ctrl+Shift+K（或转发给 TG Bot）
想找东西   → 打开知识库网页搜，或 TG 发 /search 词
想问问题   → TG 发 /ask 你的问题（基于库内内容回答）
每天 21:00 → TG 收日报，看看今天进了什么新货
```

## 1. 网页版知识库 `http://<服务器IP>:8000`

- **登录**：首次打开弹出输入框，粘贴 KB_TOKEN（在服务器 `/opt/kb/.env`），浏览器会记住
- **搜索**：顶部输入框，全文 + 语义混合检索；支持中文长句
- **来源筛选**：下拉框 clip(剪藏)/rss(订阅)/tg(TG收藏)/doc(文档)/book(电子书)
- **✚ 按钮**：手动剪藏——粘贴标题、URL、正文，提交即入库
- **↺ 按钮**：刷新最新列表
- **点击条目** → 详情弹窗：
  - 知识卡：一句话结论 / 要点 / 原文金句 / 标签 / 值得深挖的问题
  - 深度加工后还有：批判性检视 / 延伸推论 / 落地建议 / 闪卡
  - 点**标签** = 按该标签搜索；**删除**按钮移除条目；**原文**折叠展开
- 页头实时显示：已入库条数 + 累计消耗 tokens

## 2. 网页剪藏（篡改猴 kb-clipper）

- **剪藏**：选中一段文字（≥20 字）→ `Ctrl+Shift+K`；不选中则剪整页正文
- **成功提示**：`✅ 已入库 #编号，知识卡已生成`（同步生成约 10 秒）
- **篡改猴菜单**：
  - `📌 剪藏（选中文字或整页）`：等效快捷键
  - `⚙️ 设置服务器/令牌`：换服务器地址或令牌时用
- **常见报错**：`401`=令牌错了（⚙️ 重设）；`网络错误`=服务器没开或防火墙没放行 8000

## 3. TG 收藏箱 Bot（@你的bot）

| 操作 | 效果 |
|------|------|
| 转发**链接**给我 | 抓正文（境外链接走本机代理）→ 知识卡 → 入库 |
| 转发**纯文字** | 直接入库 |
| `/search 关键词` | 检索知识库，回 5 条 |
| `/ask 问题` | 基于库内内容回答，附来源引用 |
| `/digest` / `/digest 2026-11-23` | 当日（或指定日）入库清单 |
| `/stats` | 库存 + token 消耗统计 |
| （自动）每天 21:00 | 推送当日入库日报（电脑没开机则收不到） |

**运维**：
- Bot 跑在本机最小化窗口（窗口名 `kb-tg-bot`），已设开机自启
- 依赖本机代理（socks5 127.0.0.1:33210）连 Telegram——**代理软件必须开着**
- 挂了：双击 `tools/start-bot.cmd` 重启；日志 `tools/bot.log`
- 报 `409 Conflict` = 开了两个实例，任务管理器结束多余的 python

## 4. ai 命令行（本机，零依赖）

```bash
# 若已把 kb/tools 加入 PATH 可直接用 ai；否则：
py 路径\kb\tools\ai.py <子命令>

ai config                 # 交互式配置 APIKey/服务器/令牌（存 ~/.ai.json）
ai ask "问题"              # 直接问 GLM（不查库）
ai sum 长文.txt            # 深度摘要，超长自动分块汇总
ai trans 文章.md --to en   # 翻译（默认译中文）
ai prompt list            # 看提示词库
ai prompt code-review 把代码贴进来   # 套模板
ai kb "关键词"             # 检索知识库
ai kbq "问题"              # RAG 问答（带来源）
ai clip 资料.txt --source doc   # 本地文件入库（长文自动分块）
ai digest [日期]           # 日报
ai stats                  # 库存/来源/token 统计
```

自定义提示词：在 `kb/tools/prompts/` 新建 `名字.md`，正文用 `{{input}}` 占位。

## 5. 服务器管理（进阶，ssh 后 `cd /opt/kb`）

```bash
docker compose ps                      # 容器状态（kb/worker/feeds/bot）
docker compose logs -f worker          # 知识卡流水线实时日志
docker compose restart                 # 全部重启；或 restart worker/feeds/bot

# 订阅源：编辑 data/feeds.json（JSON 数组，name/url/tags），1 小时内生效
# 文档入库：把 pdf/epub/txt/md 传到 data/inbox/ 后执行
docker compose exec worker python scripts/ingest_docs.py
# 深度加工（直到新增消耗达到目标 token 数）
docker compose exec worker python scripts/deep_cards.py 2000000
# 周刊外链收割（取库内最近 N 期，抓每期外链文章）
docker compose exec worker python scripts/harvest_links.py 9

# 重置失败条目重试
docker compose exec -T worker python <<'EOF'
from app import get_db
with get_db() as db:
    print(db.execute("UPDATE items SET status='new' WHERE status='error'").rowcount)
EOF
```

**备份**（数据库 = 你的全部知识资产）：
```bash
docker compose stop worker kb        # 停写入
cp data/kb.db ~/kb-backup-$(date +%F).db
docker compose start worker kb
# 或拉回本机：scp root@<IP>:/opt/kb/data/kb.db D:\backups\
```

## 6. 故障排查速查表

| 症状 | 大概率原因 | 处理 |
|------|-----------|------|
| 网页打不开 | 服务器/防火墙 | ssh 后 `curl localhost:8000/healthz`；控制台防火墙放行 8000 |
| Bot 不回消息 | 代理没开 / 进程挂了 | 开代理；双击 start-bot.cmd；看 bot.log |
| 剪藏报 401 | 令牌不对 | 篡改猴菜单 ⚙️ 重设 |
| 条目一直"new" | APIKey 没配 / worker 挂 | `healthz` 看 key_ready；看 worker 日志 |
| 条目 error 429 | API 限流 | 有自动退避重试；严重时减少 worker 数 `--scale worker=1` |
| 搜不到刚入库的 | 知识卡还没生成 | 等几十秒，队列会消化 |
| `/ask` 很慢 | 限流排队 | 属正常，批量任务会自动让路 |

## 7. 代码更新与发布

- 仓库 = 本机 `kb/` 目录（git）。改动后 commit，推送用 GitHub 设备码授权
- `.env`、`data/` 已在 .gitignore，**永远不会**把密钥/数据推上 GitHub
- 部署方式：改完 `server/` 下的文件 scp 到服务器 `/opt/kb/server/`，然后 `docker compose up -d --build`
