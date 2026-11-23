"""kb-core —— 个人知识库核心服务
FastAPI + SQLite(FTS5 全文 + 向量检索) + BigModel GLM「知识卡」流水线。

环境变量:
  KB_DB            SQLite 路径 (默认 /data/kb.db)
  KB_API_BASE      BigModel OpenAI 兼容端点
  BIGMODEL_API_KEY 智谱 API Key
  KB_CHAT_MODEL    摘要模型 (默认 glm-5.3-flash)
  KB_EMBED_MODEL   向量模型 (默认 embedding-3)
  KB_EMBED_DIM     向量维度 (默认 1024)
  KB_TOKEN         访问令牌; 留空则首启自动生成到 data/.token
  KB_MAX_CHARS     送入摘要模型的正文字符上限
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Optional

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

CFG = dict(
    db=os.environ.get("KB_DB", "/data/kb.db"),
    api_base=os.environ.get("KB_API_BASE", "https://open.bigmodel.cn/api/paas/v4"),
    api_key=os.environ.get("BIGMODEL_API_KEY", ""),
    chat_model=os.environ.get("KB_CHAT_MODEL", "glm-5.3-flash"),
    embed_model=os.environ.get("KB_EMBED_MODEL", "embedding-3"),
    embed_dim=int(os.environ.get("KB_EMBED_DIM", "1024")),
    token=os.environ.get("KB_TOKEN", ""),
    max_chars=int(os.environ.get("KB_MAX_CHARS", "30000")),
)

# ---------------------------------------------------------------- DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL DEFAULT '',
  url_hash TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'clip',
  raw_text TEXT NOT NULL DEFAULT '',
  fts_text TEXT NOT NULL DEFAULT '',
  card_json TEXT NOT NULL DEFAULT '',
  tags_json TEXT NOT NULL DEFAULT '[]',
  embedding BLOB,
  status TEXT NOT NULL DEFAULT 'new',
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at);
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
  title, fts_text, content='items', content_rowid='id', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS items_fts_ai AFTER INSERT ON items BEGIN
  INSERT INTO items_fts(rowid,title,fts_text) VALUES(new.id,new.title,new.fts_text);
END;
CREATE TRIGGER IF NOT EXISTS items_fts_ad AFTER DELETE ON items BEGIN
  INSERT INTO items_fts(items_fts,rowid,title,fts_text)
  VALUES('delete',old.id,old.title,old.fts_text);
END;
CREATE TRIGGER IF NOT EXISTS items_fts_au AFTER UPDATE OF title,fts_text ON items BEGIN
  INSERT INTO items_fts(items_fts,rowid,title,fts_text)
  VALUES('delete',old.id,old.title,old.fts_text);
  INSERT INTO items_fts(rowid,title,fts_text) VALUES(new.id,new.title,new.fts_text);
END;
CREATE TABLE IF NOT EXISTS usage_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  kind TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0
);
"""


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(CFG["db"], timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    return con


@contextmanager
def get_db():
    con = _connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with get_db() as db:
        db.executescript(SCHEMA)


def ensure_token():
    """KB_TOKEN 未配置时生成随机令牌并持久化。"""
    if not CFG["token"]:
        p = os.path.join(os.path.dirname(CFG["db"]), ".token")
        if os.path.exists(p):
            CFG["token"] = open(p, encoding="utf-8").read().strip()
        else:
            CFG["token"] = secrets.token_hex(16)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(CFG["token"])


# ---------------------------------------------------------------- 文本工具

_CJK = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_CJK_BOUND = re.compile(rf"(?<=[{_CJK}])(?=[{_CJK}])")


def cjk_space(s: str) -> str:
    """在中文字符间插入空格，使 unicode61 分词器可按字索引中文。"""
    return _CJK_BOUND.sub(" ", s or "")


def fts_query(q: str) -> str:
    """短查询按短语匹配；长查询拆 CJK 二字滑窗 OR 提升召回（向量限流时仍可检索）。"""
    sq = cjk_space(q).replace('"', '""')
    runs = re.findall(rf"[{_CJK}][{_CJK}]{{2,}}", q.replace(" ", ""))
    if not runs:
        return '"' + sq + '"'
    grams, seen = [], set()
    for r in runs:
        for i in range(len(r) - 1):
            g = r[i:i + 2]
            if g not in seen:
                seen.add(g)
                grams.append(g)
    return " OR ".join('"' + cjk_space(g) + '"' for g in grams[:20])


def content_hash(url: str, text: str) -> str:
    if url.strip():
        return hashlib.sha1(("u:" + url.strip()).encode()).hexdigest()
    return hashlib.sha1(("t:" + text.strip()[:2000]).encode()).hexdigest()


# ---------------------------------------------------------------- 向量索引

class VecIndex:
    """内存中的暴力余弦检索：个人库规模（<10万条）完全够用且零依赖。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.ids: list[int] = []
        self.mat: Optional[np.ndarray] = None
        self.dirty = True

    def load(self):
        with get_db() as db:
            rows = db.execute(
                "SELECT id, embedding FROM items WHERE embedding IS NOT NULL"
            ).fetchall()
        ids, vecs = [], []
        for r in rows:
            ids.append(r["id"])
            vecs.append(np.frombuffer(r["embedding"], dtype=np.float32))
        mat = np.stack(vecs) if vecs else np.zeros((0, CFG["embed_dim"]), np.float32)
        with self.lock:
            self.ids, self.mat, self.dirty = ids, mat, False

    def add(self, iid: int, vec: np.ndarray):
        with self.lock:
            if self.mat is None or self.mat.shape[0] != len(self.ids):
                self.dirty = True
                return
            if self.mat.shape[0] and self.mat.shape[1] != len(vec):
                self.dirty = True
                return
            self.ids.append(iid)
            self.mat = np.vstack([self.mat, vec.astype(np.float32)])

    def _ensure(self):
        """缓存与库内向量数不一致时自动重建（worker 运行在独立容器）。"""
        try:
            with get_db() as db:
                n = db.execute("SELECT COUNT(*) c FROM items "
                               "WHERE embedding IS NOT NULL").fetchone()["c"]
            if n != len(self.ids) or self.dirty or self.mat is None:
                self.load()
        except Exception:
            self.dirty = True

    def search(self, qvec: np.ndarray, k: int = 30) -> list[tuple[int, float]]:
        self._ensure()
        with self.lock:
            if not self.ids:
                return []
            ids, mat = list(self.ids), self.mat.copy()
        q = qvec.astype(np.float32)
        n = np.linalg.norm(mat, axis=1) * np.linalg.norm(q)
        n[n == 0] = 1e-9
        sims = (mat @ q) / n
        top = np.argsort(-sims)[:k]
        return [(ids[i], float(sims[i])) for i in top]


VEC = VecIndex()

# ---------------------------------------------------------------- BigModel 客户端

_http: Optional[httpx.Client] = None


def http() -> httpx.Client:
    global _http
    if _http is None:
        _http = httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0))
    return _http


def _headers() -> dict:
    return {"Authorization": f"Bearer {CFG['api_key']}",
            "Content-Type": "application/json"}


def _log_usage(kind: str, model: str, usage: dict):
    with get_db() as db:
        db.execute(
            "INSERT INTO usage_log(kind,model,prompt_tokens,completion_tokens) "
            "VALUES(?,?,?,?)",
            (kind, model,
             usage.get("prompt_tokens") or 0,
             usage.get("completion_tokens") or 0))


def _post_retry(url: str, payload: dict, headers: dict, tries: int = 4):
    """429 限流退避重试（BigModel 并发限制）。"""
    delay = 2
    for _ in range(tries):
        r = http().post(url, json=payload, headers=headers)
        if r.status_code != 429:
            return r
        wait = int(r.headers.get("retry-after") or delay)
        time.sleep(min(wait, 20))
        delay = min(delay * 2, 20)
    return r


def bm_chat(messages: list[dict], *, json_mode: bool = False,
            max_tokens: int = 1800, temperature: float = 0.3) -> str:
    payload: dict[str, Any] = {
        "model": CFG["chat_model"], "messages": messages,
        "temperature": temperature, "max_tokens": max_tokens}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    r = _post_retry(f"{CFG['api_base']}/chat/completions", payload, _headers())
    if r.status_code >= 400 and json_mode:
        payload.pop("response_format", None)
        r = _post_retry(f"{CFG['api_base']}/chat/completions", payload, _headers())
    r.raise_for_status()
    data = r.json()
    _log_usage("chat", payload["model"], data.get("usage") or {})
    return data["choices"][0]["message"]["content"]


def bm_embed(texts: list[str], tries: int = 4) -> list[list[float]]:
    payload: dict[str, Any] = {"model": CFG["embed_model"], "input": texts}
    r = _post_retry(f"{CFG['api_base']}/embeddings",
                    {**payload, "dimensions": CFG["embed_dim"]}, _headers(), tries)
    if r.status_code >= 400:
        r = _post_retry(f"{CFG['api_base']}/embeddings", payload, _headers(), tries)
    r.raise_for_status()
    data = r.json()
    _log_usage("embed", payload["model"], data.get("usage") or {})
    out = sorted(data["data"], key=lambda d: d.get("index", 0))
    return [d["embedding"] for d in out]


# ---------------------------------------------------------------- 知识卡流水线

CARD_PROMPT = """你是个人知识库的资深编辑。阅读材料后输出一张「知识卡」，严格只输出一个 JSON 对象（禁止 markdown 代码块、禁止任何多余文字），字段：
{{"tldr": "一句话核心结论，不超过60字",
"points": ["要点", "..."],
"quotes": ["原文金句"],
"tags": ["主题标签"],
"questions": ["值得深挖的问题"]}}
要求：points 为 3~8 条、每条不超过 50 字；quotes 最多 3 条且必须是材料中确有的出色原句；tags 3~6 个，含义与标签池相同时必须复用原写法；questions 2~3 个。
标签池：{tags}
标题：{title}
正文（可能被截断）：
{body}"""


def top_tags(limit: int = 60) -> list[str]:
    with get_db() as db:
        rows = db.execute(
            "SELECT j.value AS tag, COUNT(*) c FROM items, "
            "json_each(items.tags_json) j WHERE items.status='done' "
            "GROUP BY tag ORDER BY c DESC LIMIT ?", (limit,)).fetchall()
    return [r["tag"] for r in rows]


def parse_card(text: str) -> Optional[dict]:
    if not text:
        return None
    t = re.sub(r"```(?:json)?", "", text).strip()
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        card = json.loads(t[a:b + 1])
        card.setdefault("tldr", "")
        for k in ("points", "quotes", "tags", "questions"):
            card.setdefault(k, [])
        return card
    except Exception:
        return None


def process_item(iid: int) -> dict:
    """单条流水线：知识卡摘要 → 向量 → 入库。"""
    with get_db() as db:
        row = db.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
    if row is None:
        raise KeyError(f"item {iid} not found")
    try:
        if not CFG["api_key"]:
            raise RuntimeError("BIGMODEL_API_KEY 未配置")
        body = (row["raw_text"] or "")[: CFG["max_chars"]]
        prompt = CARD_PROMPT.format(
            tags="、".join(top_tags()) or "（暂无）",
            title=row["title"] or "（无标题）", body=body)
        raw = bm_chat([{"role": "user", "content": prompt}], json_mode=True)
        card = parse_card(raw) or {
            "tldr": body[:120], "points": [], "quotes": [],
            "tags": [], "questions": []}
        emb = bm_embed([(row["title"] + "\n" + body)[:6000]])[0]
        fts_text = cjk_space(
            (row["title"] or "") + "\n" + body + "\n"
            + json.dumps(card, ensure_ascii=False))
        with get_db() as db:
            db.execute(
                "UPDATE items SET card_json=?, tags_json=?, embedding=?, "
                "fts_text=?, status='done', error='', "
                "updated_at=datetime('now','localtime') WHERE id=?",
                (json.dumps(card, ensure_ascii=False),
                 json.dumps(card.get("tags") or [], ensure_ascii=False),
                 np.asarray(emb, dtype=np.float32).tobytes(),
                 fts_text, iid))
        VEC.add(iid, np.asarray(emb, dtype=np.float32))
        return card
    except Exception as e:
        with get_db() as db:
            db.execute(
                "UPDATE items SET status='error', error=? WHERE id=?",
                (str(e)[:500], iid))
        raise


# ---------------------------------------------------------------- API

@asynccontextmanager
async def lifespan(_: FastAPI):
    os.makedirs(os.path.dirname(CFG["db"]), exist_ok=True)
    init_db()
    ensure_token()
    VEC.load()
    yield


app = FastAPI(title="kb-core", lifespan=lifespan)


def auth(request: Request, token: str = Query(default="")):
    got = request.headers.get("x-api-token") or token
    if not CFG["token"] or got != CFG["token"]:
        raise HTTPException(401, "unauthorized")


@app.get("/healthz")
def healthz():
    with get_db() as db:
        n = db.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
    return {"ok": True, "items": n, "chat_model": CFG["chat_model"],
            "key_ready": bool(CFG["api_key"])}


class ClipIn(BaseModel):
    text: str
    url: str = ""
    title: str = ""
    source: str = "clip"
    sync: bool = True


@app.post("/v1/clip")
def clip(inp: ClipIn, request: Request, token: str = Query(default="")):
    auth(request, token)
    text = inp.text.strip()
    if len(text) < 20:
        raise HTTPException(400, "text too short (<20 chars)")
    title = inp.title.strip() or text.strip().splitlines()[0][:80]
    h = content_hash(inp.url, text)
    with get_db() as db:
        cur = db.execute(
            "INSERT OR IGNORE INTO items(url,url_hash,title,source,raw_text) "
            "VALUES(?,?,?,?,?)",
            (inp.url.strip(), h, title, inp.source.strip() or "clip", text))
        if cur.rowcount == 0:
            rid = db.execute("SELECT id FROM items WHERE url_hash=?",
                             (h,)).fetchone()["id"]
            return {"id": rid, "dup": True, "card": None}
        rid = cur.lastrowid
    if not inp.sync:
        return {"id": rid, "dup": False, "card": None, "queued": True}
    card = process_item(rid)
    return {"id": rid, "dup": False, "card": card}


_TLDR = "json_extract(COALESCE(NULLIF(card_json,''),'{}'),'$.tldr') tldr"

def do_search(q: str, k: int = 12) -> list:
    """混合检索（FTS5 + 向量 + LIKE 兜底），供 API 与 TG Bot 复用。"""
    q = q.strip()
    if not q:
        return []
    rrf: dict[int, float] = {}
    snips: dict[int, str] = {}
    with get_db() as db:
        try:
            rows = db.execute(
                "SELECT rowid, snippet(items_fts,1,'<mark>','</mark>','…',20) s "
                "FROM items_fts WHERE items_fts MATCH ? ORDER BY rank LIMIT 50",
                (fts_query(q),)).fetchall()
            for i, r in enumerate(rows):
                rrf[r["rowid"]] = rrf.get(r["rowid"], 0) + 1.0 / (60 + i)
                snips[r["rowid"]] = r["s"]
        except sqlite3.OperationalError:
            pass
    if CFG["api_key"]:
        try:
            qv = np.asarray(bm_embed([q[:1000]], tries=2)[0], dtype=np.float32)
            for j, (iid, score) in enumerate(VEC.search(qv, 30)):
                rrf[iid] = rrf.get(iid, 0) + 1.0 / (60 + j)
        except Exception:
            pass
    if not rrf:  # LIKE 兜底（未生成知识卡的条目也可命中）
        with get_db() as db:
            rows = db.execute(
                "SELECT id FROM items WHERE raw_text LIKE ? OR title LIKE ? "
                "ORDER BY id DESC LIMIT ?",
                (f"%{q}%", f"%{q}%", k)).fetchall()
            rrf = {r["id"]: 1.0 for r in rows}
    top = sorted(rrf.items(), key=lambda x: -x[1])[:k]
    out = []
    with get_db() as db:
        for iid, score in top:
            r = db.execute(
                f"SELECT id,title,source,created_at,tags_json,{_TLDR} FROM items "
                "WHERE id=?", (iid,)).fetchone()
            if r is None:
                continue
            out.append(dict(r) | {"tags": json.loads(r["tags_json"]),
                                  "score": round(score, 4),
                                  "snippet": snips.get(iid, "")})
    return out


@app.get("/v1/search")
def search(request: Request, q: str, k: int = 12,
           token: str = Query(default="")):
    auth(request, token)
    return {"items": do_search(q, k)}


@app.get("/v1/items")
def list_items(request: Request, status: str = "done", source: str = "",
               limit: int = 30, offset: int = 0, token: str = Query(default="")):
    auth(request, token)
    sql = (f"SELECT id,title,source,status,created_at,tags_json,{_TLDR} FROM items")
    cond, params = [], []
    if status != "*":
        cond.append("status=?"); params.append(status)
    if source:
        cond.append("source=?"); params.append(source)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params += [min(limit, 100), offset]
    with get_db() as db:
        rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    for r in rows:
        r["tags"] = json.loads(r.pop("tags_json"))
    return {"items": rows}


@app.get("/v1/item/{iid}")
def get_item(iid: int, request: Request, token: str = Query(default="")):
    auth(request, token)
    with get_db() as db:
        r = db.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
    if r is None:
        raise HTTPException(404, "not found")
    d = dict(r)
    d.pop("embedding", None)
    d["tags"] = json.loads(d.pop("tags_json"))
    d["card"] = json.loads(d.pop("card_json") or "{}")
    return d


@app.delete("/v1/item/{iid}")
def del_item(iid: int, request: Request, token: str = Query(default="")):
    auth(request, token)
    with get_db() as db:
        cur = db.execute("DELETE FROM items WHERE id=?", (iid,))
    return {"deleted": cur.rowcount}


@app.post("/v1/item/{iid}/reprocess")
def reprocess(iid: int, request: Request, token: str = Query(default="")):
    auth(request, token)
    with get_db() as db:
        db.execute("UPDATE items SET status='new' WHERE id=?", (iid,))
    return {"id": iid, "card": process_item(iid)}


def do_digest(date: str = "") -> dict:
    """某日入库清单（TG 日报数据源）。"""
    date = date or time.strftime("%Y-%m-%d")
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            f"SELECT id,title,source,url,tags_json,{_TLDR} FROM items "
            "WHERE status='done' AND date(created_at)=? ORDER BY id", (date,))]
    for r in rows:
        r["tags"] = json.loads(r.pop("tags_json"))
    return {"date": date, "count": len(rows), "items": rows}


@app.get("/v1/digest")
def digest(request: Request, date: str = "", token: str = Query(default="")):
    auth(request, token)
    return do_digest(date)


class AskIn(BaseModel):
    q: str


@app.post("/v1/ask")
def ask(body: AskIn, request: Request, token: str = Query(default="")):
    """RAG 问答：知识库检索 top5 → GLM 归纳作答（带来源）。"""
    auth(request, token)
    if not CFG["api_key"]:
        raise HTTPException(503, "BIGMODEL_API_KEY 未配置")
    hits = do_search(body.q, 5)
    ctx_parts, srcs = [], []
    with get_db() as db:
        for i, h in enumerate(hits, 1):
            r = db.execute(
                "SELECT url, substr(raw_text,1,1500) head FROM items WHERE id=?",
                (h["id"],)).fetchone()
            if r is None:
                continue
            ctx_parts.append(f"[{i}] {h['title']} ({h['source']}) {r['url']}\n"
                             f"{h.get('tldr') or ''}\n{r['head']}")
            srcs.append({"id": h["id"], "title": h["title"], "url": r["url"]})
    if not ctx_parts:
        return {"answer": "知识库里还没有相关内容，先入库几篇再来问吧。", "sources": []}
    ans = bm_chat([{"role": "user", "content":
        "基于以下知识库片段回答问题。用中文：先给结论，再列依据；"
        "片段信息不足时明确说明。引用来源用 [n] 标注。\n\n"
        + "\n\n".join(ctx_parts) + f"\n\n问题：{body.q}"}],
        max_tokens=1200, temperature=0.3)
    return {"answer": ans, "sources": srcs}


@app.get("/v1/tags")
def tags(request: Request, limit: int = 100, token: str = Query(default="")):
    auth(request, token)
    with get_db() as db:
        rows = db.execute(
            "SELECT j.value tag, COUNT(*) c FROM items, "
            "json_each(items.tags_json) j WHERE items.status='done' "
            "GROUP BY tag ORDER BY c DESC LIMIT ?", (min(limit, 300),)).fetchall()
    return {"tags": [{"tag": r["tag"], "count": r["c"]} for r in rows]}


@app.get("/v1/stats")
def stats(request: Request, token: str = Query(default="")):
    auth(request, token)
    with get_db() as db:
        by_status = {r[0]: r[1] for r in
                     db.execute("SELECT status,COUNT(*) FROM items GROUP BY status")}
        by_source = {r[0]: r[1] for r in
                     db.execute("SELECT source,COUNT(*) FROM items GROUP BY source")}
        tok: dict[str, dict[str, int]] = {}
        total = 0
        for model, kind, p, c in db.execute(
                "SELECT model,kind,SUM(prompt_tokens),SUM(completion_tokens) "
                "FROM usage_log GROUP BY model,kind"):
            tok.setdefault(model, {})[kind] = (p or 0) + (c or 0)
            total += (p or 0) + (c or 0)
    try:
        size = os.path.getsize(CFG["db"])
    except OSError:
        size = 0
    return {"items": {"total": sum(by_status.values()), **by_status},
            "sources": by_source, "tokens": {"by_model": tok, "total": total},
            "db_bytes": size}


# ---------------------------------------------------------------- Web UI

@app.get("/", response_class=HTMLResponse)
def ui():
    return _HTML


_HTML = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>KB · 个人知识库</title><style>
:root{--bg:#0f1115;--card:#171a21;--line:#262b36;--fg:#e6e9f0;--dim:#8b93a5;--acc:#5b8cff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.6 -apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:880px;margin:0 auto;padding:26px 16px}
h1{font-size:20px;margin:0}.dim{color:var(--dim);font-size:13px}
.bar{display:flex;gap:8px;margin:16px 0 8px}
input,select,textarea,button{background:var(--card);border:1px solid var(--line);
color:var(--fg);border-radius:8px;padding:9px 12px;font:inherit}
input[type=search],input[type=text]{flex:1;min-width:0}
button{cursor:pointer}button:hover{border-color:var(--acc)}
.stats{display:flex;gap:14px;flex-wrap:wrap;margin:6px 0 14px}
.item{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:13px 16px;margin:10px 0;cursor:pointer}.item:hover{border-color:var(--acc)}
.item .t{font-weight:600}.item .s{font-size:13px;color:var(--dim);margin-top:4px}
.item .m{font-size:12px;color:var(--dim);margin-top:6px}
mark{background:#3a2b00;color:#ffd76a}
dialog{background:var(--card);color:var(--fg);border:1px solid var(--line);
border-radius:14px;max-width:780px;width:92vw;padding:22px}
dialog::backdrop{background:rgba(0,0,0,.65)}
pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);
border:1px solid var(--line);border-radius:8px;padding:10px;font-size:13px;
max-height:280px;overflow:auto}li{margin:4px 0}
.chip{display:inline-block;background:var(--bg);border:1px solid var(--line);
border-radius:999px;padding:0 10px;font-size:12px;color:var(--dim);
margin:0 6px 4px 0;cursor:pointer}
form.clip input,form.clip textarea{width:100%;margin-bottom:6px}
</style></head><body><div class=wrap>
<h1>📚 KB · 个人知识库</h1>
<div class=dim>GLM 知识卡流水线 —— <span id=stat>加载中…</span></div>
<div class=bar>
<input id=q type=search placeholder="搜索知识库（全文 + 语义混合检索）…">
<select id=src><option value=>全部来源</option><option>clip</option>
<option>rss</option><option>tg</option><option>doc</option><option>book</option></select>
<button onclick=doSearch()>搜索</button>
<button onclick=toggleClip() title=手动剪藏>✚</button>
<button onclick=loadList() title=最新列表>↺</button></div>
<form class=clip id=clipbox style=display:none onsubmit="return doClip(event)">
<input id=c_title type=text placeholder="标题（可留空，默认取首行）">
<input id=c_url type=text placeholder="来源 URL（可留空）">
<textarea id=c_text placeholder="粘贴正文（≥20字）…"></textarea>
<button>入库并生成知识卡</button> <span class=dim id=c_msg></span></form>
<div class="stats dim" id=stats></div><div id=list></div></div>
<dialog id=dlg></dialog><script>
const $=s=>document.querySelector(s);
let TOKEN=localStorage.kbtoken||"";
if(!TOKEN){TOKEN=prompt("输入访问令牌（服务器 /opt/kb/.env 中 KB_TOKEN）")||"";
localStorage.kbtoken=TOKEN;}
async function api(p,o={}){o.headers=Object.assign({"X-API-Token":TOKEN,
"Content-Type":"application/json"},o.headers||{});const r=await fetch(p,o);
if(r.status==401){localStorage.removeItem("kbtoken");location.reload();throw 0;}
return r.json();}
const esc=s=>(s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function cardHTML(d,snippet){
  const tl=snippet?snippet:esc(d.tldr||"");
  return `<div class=item onclick=view(${d.id})><div class=t>${esc(d.title)}</div>
  <div class=s>${tl}</div><div class=m>#${d.id} · ${esc(d.source)} · ${d.created_at}
  ${(d.tags||[]).slice(0,4).map(t=>` <span class=chip>${esc(t)}</span>`).join("")}</div></div>`;}
async function loadStats(){try{const s=await api("/v1/stats");
$("#stat").textContent=`${(s.items.done||0)} 条已入库 · 累计消耗 ${(s.tokens.total/1e6).toFixed(2)}M tokens`;
$("#stats").innerHTML=Object.entries(s.sources).map(([k,v])=>`<span class=chip>${k}: ${v}</span>`).join("");}catch(e){}}
async function loadList(){const d=await api("/v1/items?limit=40");
$("#list").innerHTML=d.items.map(x=>cardHTML(x)).join("")||"<p class=dim>空空如也，去剪藏第一条吧。</p>";}
async function doSearch(){const q=$("#q").value.trim();if(!q)return loadList();
const src=$("#src").value;const d=await api("/v1/search?q="+encodeURIComponent(q)+"&k=20");
let items=d.items.filter(x=>!src||x.source==src);
$("#list").innerHTML=items.map(x=>cardHTML(x,x.snippet)).join("")||"<p class=dim>无结果。</p>";}
async function view(id){const d=await api("/v1/item/"+id);const c=d.card||{};
const li=a=>(a||[]).map(x=>`<li>${esc(x)}</li>`).join("");
$("#dlg").innerHTML=`<h2 style="margin-top:0">${esc(d.title)}</h2>
<p class=dim>#${d.id} · ${esc(d.source)} · ${d.created_at} · ${esc(d.url||"本地内容")}
${d.status!="done"?` · 状态:${d.status} ${esc(d.error)}`:""}</p>
<p><b>${esc(c.tldr||"")}</b></p><ul>${li(c.points)}</ul>
${(c.quotes||[]).length?`<blockquote style="border-left:3px solid var(--acc);margin:10px 0;padding:2px 12px;color:var(--dim)">${c.quotes.map(q=>`<p>「${esc(q)}」</p>`).join("")}</blockquote>`:""}
<div>${(c.tags||[]).map(t=>`<span class=chip onclick='qsearch("${esc(t)}")'>${esc(t)}</span>`).join("")}</div>
<h3>值得深挖</h3><ul>${li(c.questions)}</ul>
<details><summary class=dim>原文</summary><pre>${esc(d.raw_text)}</pre></details>
<p><button onclick=del1(${d.id})>删除</button> <button onclick=dlg.close()>关闭</button></p>`;
dlg.showModal();}
function qsearch(t){dlg.close();$("#q").value=t;doSearch();}
async function del1(id){if(!confirm("确认删除？"))return;await api("/v1/item/"+id,{method:"DELETE"});
dlg.close();loadList();loadStats();}
function toggleClip(){const b=$("#clipbox");b.style.display=b.style.display=="none"?"block":"none";}
async function doClip(e){e.preventDefault();const t=$("#c_text").value.trim();
if(t.length<20){$("#c_msg").textContent="正文太短";return false;}
$("#c_msg").textContent="处理中（摘要+向量，约10秒）…";
const r=await api("/v1/clip",{method:"POST",body:JSON.stringify({
text:t,url:$("#c_url").value.trim(),title:$("#c_title").value.trim(),source:"clip"})});
$("#c_msg").textContent=r.dup?`重复，已存在 #${r.id}`:`✅ 已入库 #${r.id}`;
if(!r.dup){$("#c_text").value="";$("#c_url").value="";$("#c_title").value="";
loadStats();setTimeout(loadList,2000);}return false;}
$("#q").addEventListener("keydown",e=>{if(e.key=="Enter")doSearch();});
loadStats();loadList();
</script></body></html>"""
