"""深度知识卡生成器（M7 深度旋钮）。

对已生成基础知识卡的条目追加两轮深加工（真实价值 + 精准烧 token）：
  1. 深度解读：批判性分析、延伸推论、实用落地建议
  2. 关联网络：与库内已有标签/条目交叉对比，产出 3 张闪卡

用法（服务器上，目标是本次运行新增消耗的 token 数）:
  docker compose exec -T worker python scripts/deep_cards.py 3000000
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import bm_chat, get_db  # noqa: E402

DATA = os.environ.get("KB_DATA_DIR", "/data")
CURSOR = os.path.join(DATA, "deep_cursor")


def spent() -> int:
    with get_db() as db:
        return db.execute(
            "SELECT COALESCE(SUM(prompt_tokens+completion_tokens),0) t FROM usage_log"
        ).fetchone()["t"]


def pick_next() -> dict:
    """取一篇未深加工的条目（游标顺序，保证可断点续跑）。"""
    last = 0
    if os.path.exists(CURSOR):
        last = int(open(CURSOR).read().strip() or 0)
    with get_db() as db:
        row = db.execute(
            "SELECT id,title,raw_text,card_json FROM items "
            "WHERE status='done' AND id>? AND raw_text<>'' ORDER BY id LIMIT 1",
            (last,)).fetchone()
    return dict(row) if row else {}


def mark(id_: int):
    with open(CURSOR, "w") as f:
        f.write(str(id_))


def main():
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    start = spent()
    print(f"[deep] 目标新增 {target:,} tokens，当前累计 {start:,}", flush=True)
    n = 0
    wrapped = False
    while spent() - start < target:
        it = pick_next()
        if not it:
            if wrapped:
                print("[deep] 全库深加工完毕", flush=True)
                return
            wrapped = True
            mark(0)
            print("[deep] 一轮完成，回头补漏", flush=True)
            continue
        body = it["raw_text"][:15000]
        card = json.loads(it["card_json"] or "{}")
        with get_db() as db:
            tags = [r["tag"] for r in db.execute(
                "SELECT j.value tag FROM items, json_each(items.tags_json) j "
                "WHERE items.status='done' AND items.id<>? GROUP BY tag "
                "ORDER BY COUNT(*) DESC LIMIT 25", (it["id"],)).fetchall()]
        try:
            deep = bm_chat([{"role": "user", "content":
                f"基于以下材料与已有知识卡，输出深度解读（中文，纯文本，四个小节各2-4行）：\n"
                f"## 批判性检视\n哪些论点可靠/存疑？\n## 延伸推论\n沿着作者思路推到底会得到什么？\n"
                f"## 落地建议\n普通读者本周就能做的1-3件事\n## 一段话总结\n给一年后的我\n\n"
                f"已有知识卡：{json.dumps(card, ensure_ascii=False)[:1500]}\n\n材料：\n{body}"}],
                max_tokens=1200, temperature=0.5)
            links = bm_chat([{"role": "user", "content":
                f"这是知识库中的一篇材料。参考已有标签池：{'、'.join(tags)}\n"
                f"输出严格 JSON（无代码块）：{{\"related\": [\"与库内主题的具体关联，2-3条\"], "
                f"\"flashcards\": [{{\"q\": \"问题\", \"a\": \"答案\"}} x3]}}\n\n"
                f"标题：{it['title']}\n知识卡：{json.dumps(card, ensure_ascii=False)[:1200]}\n"
                f"材料开头：{body[:4000]}"}], json_mode=True, max_tokens=900)
            try:
                links_json = json.loads(links[links.find("{"):links.rfind("}") + 1])
            except Exception:
                links_json = {"related": [], "flashcards": []}
            card["deep"] = deep
            card["related"] = links_json.get("related", [])
            card["flashcards"] = links_json.get("flashcards", [])
            card["deep_done"] = True
            with get_db() as db:
                db.execute("UPDATE items SET card_json=? WHERE id=?",
                           (json.dumps(card, ensure_ascii=False), it["id"]))
            mark(it["id"])
            n += 1
            time.sleep(2)  # 温和限速，给交互查询让路
            if n % 5 == 0:
                print(f"[deep] 已深加工 {n} 篇，累计 {spent() - start:,} tokens", flush=True)
        except Exception as e:
            print(f"[deep] #{it['id']} 失败: {e}", flush=True)
            mark(it["id"])
    print(f"[deep] 达成目标，共 {n} 篇，新增 {spent() - start:,} tokens", flush=True)


if __name__ == "__main__":
    main()
