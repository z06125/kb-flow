#!/usr/bin/env python3
"""TG 收藏箱 Bot（本机桥接版）。

Telegram API 经本机 SOCKS5 代理访问（代理软件需保持运行）；
入库/检索/日报全部走你的 kb-core 服务器。
每天 DIGEST_HOUR 点自动推送当日知识库日报。

依赖: pip install "httpx[socks]"
配置: ~/.ai.json 增加 {"tg_token": "...", "proxy": "socks5://127.0.0.1:33210", "digest_hour": 21}
"""
import html as htmllib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CFGF = Path.home() / ".ai.json"
OWNERF = Path.home() / ".kb_tg_chat"
cfg = json.loads(CFGF.read_text("utf-8")) if CFGF.exists() else {}
TG_TOKEN = cfg.get("tg_token", "")
PROXY = cfg.get("proxy", "socks5://127.0.0.1:33210")
SERVER = cfg.get("server", "http://47.93.243.93:8000").rstrip("/")
KBT = cfg.get("kb_token", "")
DIGEST_HOUR = int(cfg.get("digest_hour", 21))
URL_RE = re.compile(r"https?://[^\s<>\"']+\S")

tg = httpx.Client(proxy=PROXY, timeout=httpx.Timeout(90.0, connect=15.0))
kb = httpx.Client(timeout=httpx.Timeout(60.0))


def api(method: str, **p) -> dict:
    try:
        r = tg.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", json=p)
        return r.json()
    except Exception as e:
        print(f"[tg] {method} err: {e}", flush=True)
        return {}


def send(cid, text: str):
    api("sendMessage", chat_id=cid, text=text[:4000], disable_web_page_preview=True)


def kbreq(method: str, path: str, payload=None):
    headers = {"X-API-Token": KBT}
    r = kb.request(method, SERVER + path, json=payload, headers=headers)
    return r.json()


def owner() -> str:
    return OWNERF.read_text().strip() if OWNERF.exists() else ""


def strip_tags(s: str) -> str:
    s = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", s or "", flags=re.I)
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", s)).strip()


def fetch_article(url: str) -> str:
    """境外链接走本机代理抓正文（服务器抓不到的这里都能抓）。"""
    try:
        r = tg.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                   follow_redirects=True, timeout=30)
        try:
            import trafilatura
            text = (trafilatura.extract(r.text) or "").strip()
            if len(text) >= 100:
                return text
        except ImportError:
            pass
        return strip_tags(r.text)[:20000]
    except Exception:
        return ""


def handle(msg: dict):
    cid = str(msg["chat"]["id"])
    text = (msg.get("text") or msg.get("caption") or "").strip()

    if text.startswith("/start"):
        OWNERF.write_text(cid)
        send(cid, "✅ 已绑定。\n随时把消息/链接转发给我 → 自动抓取、生成知识卡、入库\n"
                  "/search 词 · /ask 问题 · /digest · /stats")
        return
    if not owner() or cid != owner():
        if not owner():
            send(cid, "请主人先发 /start 绑定")
        return

    if text.startswith("/search"):
        q = text[len("/search"):].strip()
        items = kbreq("GET", f"/v1/search?q={httpx.QueryParams({'q': q})['q']}&k=5")["items"] if q else []
        if not items:
            send(cid, f"「{q}」无结果")
        else:
            send(cid, "\n\n".join(f"#{i['id']}·{i['source']} <b>{i['title']}</b>\n{i.get('tldr') or ''}"
                                  for i in items))
    elif text.startswith("/ask"):
        q = text[len("/ask"):].strip()
        if not q:
            send(cid, "用法: /ask 你的问题（基于知识库回答）")
        else:
            send(cid, "检索知识库中…")
            d = kbreq("POST", "/v1/ask", {"q": q})
            src = "\n".join(f"[{n+1}] {s['title']}" for n, s in enumerate(d.get("sources", [])))
            send(cid, f"{d.get('answer','')}\n\n参考:\n{src}")
    elif text.startswith("/digest"):
        d = kbreq("GET", "/v1/digest" + (f"?date={text[len('/digest'):].strip()}"
                                        if len(text) > 7 else ""))
        send(cid, "\n".join([f"📖 {d['date']} 入库 {d['count']} 条"]
                            + [f"· {i['title']}" for i in d["items"][:25]]))
    elif text.startswith("/stats"):
        s = kbreq("GET", "/v1/stats")
        t = s["tokens"]["total"]
        send(cid, f"已入库 {s['items'].get('done',0)}/{s['items']['total']} 条"
                  f" · 累计 {t:,} tokens")
    elif not text:
        send(cid, "发文本或带说明的链接给我")
    else:
        m = URL_RE.search(text)
        if m:
            url = m.group(0)
            send(cid, "抓取正文（境外链接经代理，约10秒）…")
            body = fetch_article(url)
            note = f"正文 {len(body)} 字" if len(body) >= 100 else "抓取失败，仅存消息文本"
            store = body if len(body) >= 100 else text
            title = (strip_tags(text.replace(url, ""))[:80] or url[:80])
            d = kbreq("POST", "/v1/clip", {"text": store, "title": title,
                                           "url": url, "source": "tg", "sync": False})
            send(cid, (f"重复，已在库 #{d['id']}" if d.get("dup")
                       else f"✅ #{d['id']} 入库（{note}），知识卡生成中"))
        else:
            d = kbreq("POST", "/v1/clip", {"text": text, "source": "tg", "sync": False})
            send(cid, (f"重复 #{d['id']}" if d.get("dup")
                       else f"✅ #{d['id']} 已入库，知识卡生成中"))


def digest_loop():
    sent_date = ""
    while True:
        now = datetime.now()
        if now.hour == DIGEST_HOUR and now.strftime("%Y-%m-%d") != sent_date and owner():
            try:
                d = kbreq("GET", "/v1/digest")
                send(owner(), "\n".join([f"📖 每日知识库日报 {d['date']}（{d['count']} 条）"]
                                        + [f"· {i['title']}" for i in d["items"][:20]]))
                sent_date = now.strftime("%Y-%m-%d")
            except Exception as e:
                print(f"[tg] digest err: {e}", flush=True)
        time.sleep(60)


def main():
    if not TG_TOKEN:
        sys.exit("请在 ~/.ai.json 配置 tg_token")
    me = api("getMe")
    if not me.get("ok"):
        sys.exit(f"TG 连接失败: {me}")
    print(f"[tg] bot @{me['result']['username']} online (proxy={PROXY})", flush=True)
    api("setMyCommands", commands=[
        {"command": "search", "description": "检索知识库 /search 词"},
        {"command": "ask", "description": "基于知识库回答 /ask 问题"},
        {"command": "digest", "description": "今日入库清单"},
        {"command": "stats", "description": "库存与token统计"}])
    import threading
    threading.Thread(target=digest_loop, daemon=True).start()
    offset = 0
    while True:
        d = api("getUpdates", offset=offset, timeout=50)
        for u in d.get("result") or []:
            offset = u["update_id"] + 1
            msg = u.get("message") or u.get("channel_post")
            if msg:
                try:
                    handle(msg)
                except Exception as e:
                    print(f"[tg] handle err: {e}", flush=True)


if __name__ == "__main__":
    main()
