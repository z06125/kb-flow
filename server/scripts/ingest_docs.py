"""本地文档/电子书批量入库（M7 主力工具之一）。

把文件放到服务器 /data/inbox/ 下（compose 已挂载 ./data:/data）：
  docker compose exec worker python scripts/ingest_docs.py

支持 .txt .md .pdf .epub；长文档按段落切块（默认 12000 字符/块），
每块一条 items(source=doc|book, status=new)，worker 逐块生成知识卡。
"""
import html as htmllib
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import get_db, content_hash  # noqa: E402

INBOX = os.environ.get("KB_INBOX", "/data/inbox")
CHUNK = int(os.environ.get("KB_DOC_CHUNK", "12000"))


def read_text(path: str) -> str:
    if path.lower().endswith((".txt", ".md")):
        raw = open(path, "rb").read()
        for enc in ("utf-8", "gb18030", "utf-16"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", "ignore")
    if path.lower().endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    if path.lower().endswith(".epub"):
        parts = []
        with zipfile.ZipFile(path) as z:
            for name in sorted(n for n in z.namelist()
                               if n.lower().endswith((".xhtml", ".html", ".htm"))):
                raw = z.read(name).decode("utf-8", "ignore")
                raw = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", raw, flags=re.I)
                text = htmllib.unescape(re.sub(r"<[^>]+>", "\n", raw))
                text = re.sub(r"\n{3,}", "\n\n", text).strip()
                if len(text) > 80:
                    parts.append(text)
        return "\n\n".join(parts)
    return ""


def chunks(text: str, size: int):
    paras, out, buf = text.split("\n"), [], ""
    for p in paras:
        if len(buf) + len(p) + 1 > size and buf:
            out.append(buf.strip())
            buf = ""
        buf += p + "\n"
    if buf.strip():
        out.append(buf.strip())
    return [c for c in out if len(c) >= 100]


def insert(title: str, source: str, text: str) -> bool:
    with get_db() as db:
        cur = db.execute(
            "INSERT OR IGNORE INTO items(url,url_hash,title,source,raw_text) "
            "VALUES(?,?,?,?,?)",
            ("", content_hash("", text), title, source, text))
        return cur.rowcount > 0


def main():
    if not os.path.isdir(INBOX):
        os.makedirs(INBOX, exist_ok=True)
        print(f"[docs] 已创建 {INBOX}，请把文件放进去后重跑", flush=True)
        return
    total_new = 0
    for fn in sorted(os.listdir(INBOX)):
        path = os.path.join(INBOX, fn)
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(fn)[1].lower()
        if ext not in (".txt", ".md", ".pdf", ".epub"):
            continue
        stem = os.path.splitext(fn)[0]
        source = "book" if ext == ".epub" else "doc"
        try:
            text = read_text(path)
        except Exception as e:
            print(f"[docs] {fn}: 解析失败 {e}", flush=True)
            continue
        if len(text) < 100:
            print(f"[docs] {fn}: 无有效文本（扫描版 PDF 不支持）", flush=True)
            continue
        n = 0
        for i, ck in enumerate(chunks(text, CHUNK), 1):
            title = f"{stem} · 第{i}段" if len(ck) < len(text) * 0.9 or i > 1 else stem
            if insert(title[:180], source, ck):
                n += 1
        total_new += n
        print(f"[docs] {fn}: 切块入库 {n} 块（共 {len(text)} 字）", flush=True)
        os.replace(path, path + ".done")
    print(f"[docs] 全部完成，新增 {total_new} 条，等待 worker 生成知识卡", flush=True)


if __name__ == "__main__":
    sys.exit(main())
