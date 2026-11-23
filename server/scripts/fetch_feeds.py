"""RSS/Atom 订阅采集器。

读取 /data/feeds.json 中的订阅源列表，拉取新条目 → trafilatura 正文抽取 →
写入 items(status='new')，由 worker 统一生成知识卡与向量。

feeds.json 格式:
[
  {"name": "阮一峰的网络日志", "url": "https://www.ruanyifeng.com/blog/atom.xml", "tags": ["tech"]}
]
"""
import html as htmllib
import json
import os
import re
import sys
import time

import feedparser
import httpx
import trafilatura

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import get_db, content_hash  # noqa: E402

DATA = os.environ.get("KB_DATA_DIR", "/data")
FEEDS = os.path.join(DATA, "feeds.json")
STATE = os.path.join(DATA, "feeds_state.json")
INTERVAL = int(os.environ.get("KB_FEED_INTERVAL", "3600"))
MAX_PER_FEED = int(os.environ.get("KB_FEED_MAX", "10"))
UA = "Mozilla/5.0 (compatible; kb-feeds/1.0; personal KB)"

DEFAULT_FEEDS = [
    {"name": "阮一峰的网络日志", "url": "https://www.ruanyifeng.com/blog/atom.xml"},
    {"name": "Hacker News Best", "url": "https://hnrss.org/best"},
]


def _load(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path: str, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def strip_html(s: str) -> str:
    s = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return htmllib.unescape(re.sub(r"\s{2,}", " ", s)).strip()


def fetch_article(url: str) -> str:
    """抓原文并抽取正文；失败返回空串。"""
    try:
        r = httpx.get(url, timeout=25, follow_redirects=True,
                      headers={"User-Agent": UA})
        r.raise_for_status()
        return (trafilatura.extract(r.text) or "").strip()
    except Exception:
        return ""


def fetch_once() -> dict:
    feeds = _load(FEEDS, DEFAULT_FEEDS)
    if not os.path.exists(FEEDS):
        _save(FEEDS, DEFAULT_FEEDS)
    state = _load(STATE, {})
    report = {"feeds": 0, "new": 0, "skip": 0, "fail": 0}
    for f in feeds:
        url = (f.get("url") or "").strip()
        if not url:
            continue
        report["feeds"] += 1
        try:
            r = httpx.get(url, timeout=25, follow_redirects=True,
                          headers={"User-Agent": UA})
            d = feedparser.parse(r.content)
        except Exception as e:
            print(f"[feeds] FETCH FAIL {url}: {e}", flush=True)
            report["fail"] += 1
            continue
        seen = set(state.get(url, []))
        added = 0
        for e in d.entries[:MAX_PER_FEED * 3]:
            eid = e.get("id") or e.get("link") or e.get("title")
            link = e.get("link") or ""
            title = strip_html(e.get("title") or "")[:200]
            if not eid or eid in seen or not link:
                continue
            text = fetch_article(link)
            if len(text) < 100:
                text = strip_html(e.get("summary") or e.get("description") or "")
            if len(text) < 100:
                seen.add(eid)
                report["skip"] += 1
                continue
            tags = f.get("tags") or []
            with get_db() as db:
                db.execute(
                    "INSERT OR IGNORE INTO items(url,url_hash,title,source,raw_text,tags_json) "
                    "VALUES(?,?,?,?,?,?)",
                    (link, content_hash(link, text), title, "rss", text[:100000],
                     json.dumps(tags, ensure_ascii=False)))
            seen.add(eid)
            added += 1
            report["new"] += 1
            if added >= MAX_PER_FEED:
                break
        state[url] = list(seen)[-300:]
        _save(STATE, state)
        print(f"[feeds] {f.get('name') or url}: +{added}", flush=True)
    return report


def main():
    os.makedirs(DATA, exist_ok=True)
    print(f"[feeds] started, interval={INTERVAL}s", flush=True)
    while True:
        t0 = time.time()
        try:
            rep = fetch_once()
            print(f"[feeds] round done {rep} in {time.time()-t0:.0f}s", flush=True)
        except Exception as e:
            print(f"[feeds] ROUND ERROR: {e}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
