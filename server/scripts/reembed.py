"""更换向量模型后一键重建全部向量。

用法：改好 .env 的 KB_API_BASE / BIGMODEL_API_KEY / KB_EMBED_MODEL / KB_EMBED_DIM 后，
  docker compose up -d --force-recreate
  docker compose exec worker python scripts/reembed.py
"""
import sys
import os

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import CFG, VEC, bm_embed, get_db  # noqa: E402

BATCH = 16


def main():
    with get_db() as db:
        rows = db.execute(
            "SELECT id, title, raw_text FROM items WHERE raw_text<>'' "
            "ORDER BY id").fetchall()
    print(f"[reembed] 模型={CFG['embed_model']} 维度={CFG['embed_dim']}，"
          f"待重建 {len(rows)} 条", flush=True)
    done = failed = 0
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        texts = [(r["title"] + "\n" + r["raw_text"])[:6000] for r in chunk]
        try:
            embs = bm_embed(texts)
        except Exception as e:
            print(f"[reembed] 批次失败({start}~{start+len(chunk)}): {e}", flush=True)
            failed += len(chunk)
            continue
        with get_db() as db:
            for r, emb in zip(chunk, embs):
                db.execute("UPDATE items SET embedding=? WHERE id=?",
                           (np.asarray(emb, dtype=np.float32).tobytes(), r["id"]))
        done += len(chunk)
        VEC.dirty = True
        print(f"[reembed] {done}/{len(rows)}", flush=True)
    print(f"[reembed] 完成：成功 {done}，失败 {failed}", flush=True)


if __name__ == "__main__":
    main()
