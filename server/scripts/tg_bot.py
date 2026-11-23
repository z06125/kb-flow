"""Telegram 收藏箱 Bot：收藏/转发 → 知识库；/search 检索 · /digest 日报 · /stats 统计。

服务器无法直连 api.telegram.org 时，在 .env 配置反代:
  TG_API_BASE=https://<your-worker>.workers.dev   # 见 cf-worker/tg-relay.js
"""
import os
import re
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import content_hash, do_digest, do_search, get_db  # noqa: E402

TG = os.environ.get("TG_API_BASE", "https://api.telegram.org").rstrip("/")
TOKEN = os.environ.get("TG_BOT_TOKEN", "")
DATA = os.environ.get("KB_DATA_DIR", "/data")
OWNER = os.path.join(DATA, "tg_chat_id")
URL_RE = re.compile(r"https?://[^\s<>\"']+")


def api(method: str, **payload) -> dict:
    try:
        r = httpx.post(f"{TG}/bot{TOKEN}/{method}", json=payload, timeout=90)
        return r.json()
    except Exception:
        return {}


def send(chat_id, text: str):
    api("sendMessage", chat_id=chat_id, text=text[:4000],
        disable_web_page_preview=True)


def bound_id() -> str:
    try:
        return open(OWNER, encoding="utf-8").read().strip()
    except OSError:
        return ""


def insert(text: str, title: str, url: str, source: str):
    with get_db() as db:
        cur = db.execute(
            "INSERT OR IGNORE INTO items(url,url_hash,title,source,raw_text) "
            "VALUES(?,?,?,?,?)",
            (url, content_hash(url, text), title, source, text))
        if cur.rowcount == 0:
            row = db.execute("SELECT id FROM items WHERE url_hash=?",
                             (content_hash(url, text),)).fetchone()
            return (row["id"] if row else None), True
        return cur.lastrowid, False


def stats_text() -> str:
    with get_db() as db:
        done = db.execute(
            "SELECT COUNT(*) c FROM items WHERE status='done'").fetchone()["c"]
        total = db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        tok = db.execute("SELECT COALESCE(SUM(prompt_tokens+completion_tokens),0) t "
                         "FROM usage_log").fetchone()["t"]
    return f"已入库 {done}/{total} 条 · 累计消耗 {tok:,} tokens"


def handle(msg: dict):
    chat_id = str(msg["chat"]["id"])
    text = (msg.get("text") or msg.get("caption") or "").strip()

    if text.startswith("/start"):
        os.makedirs(DATA, exist_ok=True)
        with open(OWNER, "w", encoding="utf-8") as f:
            f.write(chat_id)
        send(chat_id, "✅ 已绑定本对话。\n把任何消息/链接转发给我 → 自动入库并生成知识卡\n"
                      "/search 词 · /digest · /stats")
        return
    if not bound_id() or chat_id != bound_id():
        if not bound_id():
            send(chat_id, "请主人先发送 /start 完成绑定")
        return

    if text.startswith("/search"):
        q = text[len("/search"):].strip()
        items = do_search(q, 5) if q else []
        if not items:
            send(chat_id, f"「{q}」无结果")
            return
        lines = [f"#{i['id']} · {i['source']} · <b>{i['title']}</b>\n{i.get('tldr') or ''}"
                 for i in items]
        send(chat_id, "\n\n".join(lines))
    elif text.startswith("/digest"):
        d = do_digest(text[len("/digest"):].strip())
        lines = [f"📖 {d['date']} 入库 {d['count']} 条"]
        lines += [f"· {i['title']}" for i in d["items"][:25]]
        send(chat_id, "\n".join(lines))
    elif text.startswith("/stats"):
        send(chat_id, stats_text())
    elif not text:
        send(chat_id, "仅支持文本或带说明文字的媒体")
    else:
        m = URL_RE.search(text)
        if m:
            from scripts.fetch_feeds import fetch_article, strip_html
            url = m.group(0)
            body = fetch_article(url)
            note = (f"抓取正文 {len(body)} 字" if len(body) >= 100
                    else "正文抓取失败（境外链接需经浏览器剪藏），仅存消息文本")
            store = body if len(body) >= 100 else text
            title = strip_html(text.replace(url, ""))[:80] or url[:80]
            iid, dup = insert(store, title, url, "tg")
        else:
            iid, dup = insert(text, text.splitlines()[0][:80], "", "tg")
            note = ""
        send(chat_id, (f"重复，已在库 #{iid}" if dup
                       else f"✅ #{iid} 已入库{('（' + note + '）') if note else ''}，知识卡生成中"))


def main():
    if not TOKEN:
        print("[bot] TG_BOT_TOKEN 未配置，挂起（配置后 docker compose restart bot）",
              flush=True)
        while True:
            time.sleep(3600)
    os.makedirs(DATA, exist_ok=True)
    offset = 0
    print(f"[bot] polling via {TG}", flush=True)
    while True:
        try:
            d = api("getUpdates", offset=offset, timeout=50)
            for u in d.get("result") or []:
                offset = u["update_id"] + 1
                msg = u.get("message") or u.get("channel_post")
                if msg:
                    try:
                        handle(msg)
                    except Exception as e:
                        print(f"[bot] handle error: {e}", flush=True)
        except Exception as e:
            print(f"[bot] poll error: {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
