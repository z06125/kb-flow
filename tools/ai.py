#!/usr/bin/env python3
"""ai —— 个人 AI 命令行工具包（纯 Python 标准库，零第三方依赖）

用法:
  ai config                          配置 APIKey/服务器/令牌
  ai ask "问题"                       一次性问答
  ai sum 文件                         长文深度摘要（自动分块 map-reduce）
  ai trans 文件 [--to en]             翻译（默认译为中文，自动分块）
  ai prompt list                      列出提示词库
  ai prompt <名称> <输入>              套用 prompts/ 下的模板
  ai kb "关键词"                       检索个人知识库（混合检索）
  ai clip 文件 [--source doc]         本地文件推送进知识库
  ai digest [YYYY-MM-DD]              知识库日报
  ai stats                            知识库统计与 token 消耗

配置文件: ~/.ai.json
"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG = Path.home() / ".ai.json"
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
API_BASE = "https://open.bigmodel.cn/api/paas/v4"

try:  # Windows 控制台中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def cfg() -> dict:
    default = {"api_key": "", "model": "glm-5.3-flash",
               "server": "http://47.93.243.93:8000", "kb_token": ""}
    if CONFIG.exists():
        default.update(json.loads(CONFIG.read_text("utf-8")))
    return default


def save_cfg(c: dict):
    CONFIG.write_text(json.dumps(c, ensure_ascii=False, indent=2), "utf-8")
    print(f"已保存配置 -> {CONFIG}")


def post(url: str, payload: dict, *, token: str = "", key: str = "",
         timeout: int = 300) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-API-Token"] = token
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, json.dumps(payload).encode("utf-8"), headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get(url: str, token: str = "", timeout: int = 60) -> dict:
    headers = {"X-API-Token": token} if token else {}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def chat(c: dict, messages: list, max_tokens: int = 4000,
         temperature: float = 0.4) -> str:
    d = post(f"{API_BASE}/chat/completions",
             {"model": c.get("model", "glm-5.3-flash"), "messages": messages,
              "max_tokens": max_tokens, "temperature": temperature},
             key=c["api_key"])
    return d["choices"][0]["message"]["content"]


def read_text(path: str) -> str:
    raw = Path(path).read_bytes()
    for enc in ("utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "ignore")


def chunks(text: str, size: int = 30000) -> list:
    out, buf = [], ""
    for p in text.split("\n"):
        if len(buf) + len(p) + 1 > size and buf:
            out.append(buf.strip())
            buf = ""
        buf += p + "\n"
    if buf.strip():
        out.append(buf.strip())
    return [c for c in out if len(c) >= 40] or ([text.strip()] if text.strip() else [])


# ---------------------------------------------------------------- 子命令

def cmd_ask(c, args):
    print(chat(c, [{"role": "user", "content": " ".join(args)}]))


def cmd_sum(c, args):
    text = read_text(args[0])
    parts = chunks(text)
    if len(parts) == 1:
        print(chat(c, [{"role": "user", "content":
            "对以下内容输出深度摘要：一句话核心结论；3-8 条要点；1-3 条行动建议。\n\n" + text}]))
        return
    acc = []
    for i, p in enumerate(parts, 1):
        print(f"[块 {i}/{len(parts)}] 提取要点…", file=sys.stderr)
        acc.append(chat(c, [{"role": "user", "content":
            f"这是长文档第 {i}/{len(parts)} 块，只输出本块要点，每条一行。\n\n{p}"}]))
    print("[汇总] …", file=sys.stderr)
    print(chat(c, [{"role": "user", "content":
        "以下是一篇长文档各块的要点，汇总为总摘要：一句话核心结论；"
        "5-10 条全局要点；行动建议。用中文。\n\n" + "\n".join(acc)}]))


def cmd_trans(c, args):
    to = "en"
    if "--to" in args:
        i = args.index("--to")
        to = args[i + 1]
        args = args[:i] + args[i + 2:]
    text = read_text(args[0])
    for i, p in enumerate(chunks(text), 1):
        print(f"[块 {i}] ", file=sys.stderr)
        print(chat(c, [{"role": "user", "content":
            f"把下面内容翻译为{'英文' if to == 'en' else '中文'}，"
            f"只输出译文，保留段落结构。\n\n{p}"}], temperature=0.2))


def cmd_prompt(c, args):
    if not args or args[0] in ("list", "ls"):
        print("可用提示词：")
        for p in sorted(PROMPT_DIR.glob("*.md")):
            first = p.read_text("utf-8").splitlines()
            desc = next((l[2:] for l in first if l.startswith("> ")), "")
            print(f"  {p.stem:<16} {desc}")
        return
    name, rest = args[0], " ".join(args[1:])
    p = PROMPT_DIR / f"{name}.md"
    if not p.exists():
        sys.exit(f"模板不存在: {p}（ai prompt list 查看）")
    body = p.read_text("utf-8")
    body = body.split("---", 2)[-1] if body.startswith("---") else body
    body = body.replace("{{input}}", rest or "{{input}} 为空")
    print(chat(c, [{"role": "user", "content": body}]))


def cmd_kb(c, args):
    q = " ".join(args)
    d = get(f"{c['server']}/v1/search?q={urllib.parse.quote(q)}&k=10",
            token=c["kb_token"])
    if not d["items"]:
        print("（无结果）")
        return
    for it in d["items"]:
        print(f"#{it['id']} [{it['source']}] {it['title']}  ({it['created_at']})")
        if it.get("tldr"):
            print(f"   {it['tldr']}")
        print()


def cmd_clip(c, args):
    source = "doc"
    if "--source" in args:
        i = args.index("--source")
        source = args[i + 1]
        args = args[:i] + args[i + 2:]
    text = read_text(args[0])
    title = Path(args[0]).stem
    parts = chunks(text, 12000)
    n = 0
    for i, ck in enumerate(parts, 1):
        t = title if len(parts) == 1 else f"{title} · 第{i}段"
        post(f"{c['server']}/v1/clip",
             {"text": ck, "title": t, "source": source, "sync": False},
             token=c["kb_token"])
        n += 1
    print(f"已入库 {n} 块（{len(text)} 字），后台将生成知识卡")


def cmd_digest(c, args):
    date = args[0] if args else ""
    d = get(f"{c['server']}/v1/digest" + (f"?date={date}" if date else ""),
            token=c["kb_token"])
    print(f"# KB 日报 {d['date']}（{d['count']} 条）\n")
    for it in d["items"]:
        print(f"- **{it['title']}**  `{it['source']}`")
        if it.get("tldr"):
            print(f"  {it['tldr']}")
    if not d["items"]:
        print("（今日暂无入库）")


def cmd_stats(c, args):
    s = get(f"{c['server']}/v1/stats", token=c["kb_token"])
    print(f"条目: {s['items']}")
    print(f"来源: {s['sources']}")
    print(f"token 消耗: {s['tokens']['total']:,}")
    for m, kinds in s["tokens"]["by_model"].items():
        print(f"  {m}: {kinds}")
    print(f"数据库: {s['db_bytes']/1048576:.1f} MB")


def cmd_config(c):
    print("回车保留原值。")
    for k, tip in [("api_key", "智谱 APIKey"), ("model", f"模型[{c['model']}]"),
                   ("server", "KB 服务器"), ("kb_token", "KB 令牌")]:
        v = input(f"{tip}: ").strip()
        if v:
            c[k] = v
    save_cfg(c)


def cmd_kbq(c, args):
    q = " ".join(args)
    d = post(f"{c['server']}/v1/ask", {"q": q}, token=c["kb_token"], timeout=180)
    print(d["answer"])
    for n, s in enumerate(d.get("sources", []), 1):
        print(f"[{n}] {s['title']}  {s.get('url') or ''}")


CMDS = {"ask": cmd_ask, "sum": cmd_sum, "trans": cmd_trans,
        "prompt": cmd_prompt, "kb": cmd_kb, "kbq": cmd_kbq, "clip": cmd_clip,
        "digest": cmd_digest, "stats": cmd_stats, "config": cmd_config}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    cmd, args = sys.argv[1], sys.argv[2:]
    fn = CMDS.get(cmd)
    if not fn:
        sys.exit(f"未知命令: {cmd}\n{__doc__}")
    fn(cfg(), args)


if __name__ == "__main__":
    main()
