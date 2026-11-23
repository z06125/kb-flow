"""后台流水线：轮询处理 status='new' 的条目（知识卡摘要 + 向量化）。"""
import os
import time

from app import get_db, process_item

POLL = int(os.environ.get("KB_WORKER_POLL", "5"))
BATCH = int(os.environ.get("KB_WORKER_BATCH", "20"))


def main():
    print(f"[worker] started, poll={POLL}s", flush=True)
    while True:
        with get_db() as db:
            rows = db.execute(
                "SELECT id FROM items WHERE status='new' "
                "ORDER BY id LIMIT ?", (BATCH,)).fetchall()
        if not rows:
            time.sleep(POLL)
            continue
        for (iid,) in rows:
            t0 = time.time()
            try:
                process_item(iid)
                print(f"[worker] #{iid} done in {time.time()-t0:.1f}s", flush=True)
            except Exception as e:  # process_item 已写 error 状态
                print(f"[worker] #{iid} ERROR: {e}", flush=True)


if __name__ == "__main__":
    main()
