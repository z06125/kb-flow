"""种子页链接收割器：从页面（如阮一峰科技爱好者周刊）提取外链文章并入库。

用法（服务器上）:
  docker compose exec worker python scripts/harvest_links.py 8     # 收割库内最近8篇周刊的全部外链
"""
import json
import os
import re
import sys
import time

import httpx
import trafilatura

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import content_hash, get_db  # noqa: E402

UA = "Mozilla/5.0 (compatible; kb-harvest/1.0)"
SKIP = re.compile(r"\.(jpg|jpeg|png|gif|webp|svg|zip|rar|7z|exe|dmg|mp4|mp3|pdf)(\?|$)|"
                  r"(github\.com/[^/]+/?$|twitter\.com|x\.com/|weibo\.com|bilibili\.com|"
                  r"youtube\.com|youtu\.be|douyin|zhihu\.com/zhihu|itunes|play\.google)", re.I)


def seed_urls(limit: int) -> list:
    with get_db() as db:
        rows = db.execute(
            "SELECT url FROM items WHERE url LIKE '%ruanyifeng.com/blog/%' "
            "AND raw_text LIKE '%周刊%' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [r["url"] for r in rows]


def extract_links(html: str, base: str) -> list:
    out, seen = [], set()
    for m in re.finditer(r'href="(https?://[^"]+)"', html):
        u = m.group(1).split("#")[0].rstrip("/")
        if u == base or u in seen or SKIP.search(u):
            continue
        if "ruanyifeng.com" in u:
            continue
        seen.add(u)
        out.append(u)
    return out


def main():
    n_seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    per_seed = int(os.environ.get("KB_HARVEST_PER_SEED", "40"))
    client = httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=25)
    seeds = seed_urls(n_seeds)
    print(f"[links] 种子页 {len(seeds)} 个，每页最多 {per_seed} 链接", flush=True)
    total = 0
    for seed in seeds:
        try:
            page = client.get(seed).text
        except Exception as e:
            print(f"[links] 种子失败 {seed}: {e}", flush=True)
            continue
        links = extract_links(page, seed)[:per_seed]
        print(f"[links] {seed} -> {len(links)} 链接", flush=True)
        for u in links:
            h = content_hash(u, "x")
            with get_db() as db:
                dup = db.execute("SELECT 1 FROM items WHERE url_hash=?", (h,)).fetchone()
            if dup:
                continue
            try:
                r = client.get(u)
                text = (trafilatura.extract(r.text) or "").strip()
            except Exception:
                text = ""
            if len(text) < 150:
                continue
            m = re.search(r"<title[^>]*>([^<]{4,120})</title>", r.text)
            import html as htmllib
            title = htmllib.unescape(m.group(1)).strip() if m else u[:80]
            with get_db() as db:
                db.execute(
                    "INSERT OR IGNORE INTO items(url,url_hash,title,source,raw_text) "
                    "VALUES(?,?,?,?,?)",
                    (u, h, title, "rss", text[:100000]))
            total += 1
            time.sleep(0.3)
    print(f"[links] 完成，新增 {total} 条待处理", flush=True)


if __name__ == "__main__":
    main()
