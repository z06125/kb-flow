"""Sitemap 收割器（M7 批量烧 token 主力）。

从站点 sitemap 抓取历史文章 URL 列表 → 逐篇正文抽取 → 入库排队（worker 生成知识卡）。
断点安全：入库走 INSERT OR IGNORE（URL 哈希去重），可反复运行。

用法（服务器上）:
  docker compose exec worker python scripts/harvest_sitemap.py sspai 300
  可选站点名: sspai / 36kr / solidot / jiqizhixin
"""
import json
import os
import re
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import content_hash, get_db  # noqa: E402
from scripts.fetch_feeds import fetch_article  # noqa: E402

DATA = os.environ.get("KB_DATA_DIR", "/data")
UA = "Mozilla/5.0 (compatible; kb-harvest/1.0)"

SITEMAPS = {
    # 各站点 sitemap（文章 URL 模式各不相同，用正则筛）
    "sspai": ("https://sspai.com/sitemap.xml", r"https://sspai\.com/post/\d+"),
    "36kr": ("https://36kr.com/sitemap.xml", r"https://36kr\.com/p/\d+"),
    "solidot": ("https://www.solidot.org/sitemap.xml", r"https://www\.solidot\.org/story\?id=\d+"),
    "jiqizhixin": ("https://www.jiqizhixin.com/sitemap.xml", r"https://www\.jiqizhixin\.com/articles/\S+"),
}


def load_seen() -> set:
    try:
        with open(os.path.join(DATA, "harvest_seen.json"), encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(seen: set):
    tmp = os.path.join(DATA, "harvest_seen.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(seen)[-50000:], f)
    os.replace(tmp, os.path.join(DATA, "harvest_seen.json"))


def get_sitemap_urls(client: httpx.Client, sitemap_url: str, pattern: str,
                     limit: int = 2000) -> list:
    xml = client.get(sitemap_url, timeout=30).text
    urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    # sitemap index → 再取一层
    if urls and "<sitemapindex" in xml:
        out = []
        for sm in urls[:10]:
            try:
                sub = client.get(sm, timeout=30).text
                out += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sub)
            except Exception:
                continue
        urls = out
    picked = [u for u in urls if re.match(pattern, u)]
    return picked[-limit:]


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    cap = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    if name not in SITEMAPS:
        sys.exit(f"可用站点: {', '.join(SITEMAPS)}")
    sitemap_url, pattern = SITEMAPS[name]
    client = httpx.Client(headers={"User-Agent": UA}, follow_redirects=True)
    urls = get_sitemap_urls(client, sitemap_url, pattern)
    print(f"[harvest] {name}: sitemap 命中 {len(urls)} 篇，本次目标 {cap}", flush=True)
    seen = load_seen()
    added = failed = 0
    for url in urls:
        if added >= cap:
            break
        if url in seen:
            continue
        try:
            r = client.get(url, timeout=25)
            import trafilatura
            text = (trafilatura.extract(r.text) or "").strip()
        except Exception:
            text = ""
        seen.add(url)
        if len(text) < 150:
            failed += 1
            continue
        title = url.rsplit("/", 1)[-1][:80]
        m = re.search(r"<title[^>]*>([^<]{4,120})</title>", r.text)
        if m:
            import html as htmllib
            title = htmllib.unescape(m.group(1)).strip()
        with get_db() as db:
            db.execute(
                "INSERT OR IGNORE INTO items(url,url_hash,title,source,raw_text) "
                "VALUES(?,?,?,?,?)",
                (url, content_hash(url, text), title, "rss", text[:100000]))
        added += 1
        if added % 10 == 0:
            print(f"[harvest] {name}: +{added} (失败{failed})", flush=True)
            save_seen(seen)
        time.sleep(0.4)  # 温和抓取
    save_seen(seen)
    print(f"[harvest] {name} 完成: 新增 {added}，失败 {failed}，worker 接力", flush=True)


if __name__ == "__main__":
    main()
