"""投递箱自动同步：扫描本地文件夹 → 上传服务器 inbox → 触发入库生成知识卡。

由 Windows 计划任务每 30 分钟运行一次，也可手动运行：
  pythonw inbox_sync.py    （静默）
  python inbox_sync.py     （看输出）

配置：~/.ai.json 里的 "inbox_dir" 指定扫描文件夹。
去重：~/.kb_inbox_seen.json 记录 文件名|大小|修改时间，变更才会重传。
"""
import json
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CFGF = Path.home() / ".ai.json"
SEENF = Path.home() / ".kb_inbox_seen.json"
SERVER = "root@47.93.243.93"
REMOTE = "/opt/kb/data/inbox/"
EXTS = {".pdf", ".epub", ".txt", ".md"}


def main() -> int:
    cfg = json.loads(CFGF.read_text("utf-8")) if CFGF.exists() else {}
    inbox = Path(cfg.get("inbox_dir") or r"E:\电子书 AI文档整理")
    if not inbox.exists():
        print(f"[inbox] 文件夹不存在：{inbox}")
        return 0
    seen = json.loads(SEENF.read_text("utf-8")) if SEENF.exists() else {}

    todo = []
    for p in inbox.iterdir():
        if p.is_file() and p.suffix.lower() in EXTS:
            st = p.stat()
            key = f"{st.st_size}|{int(st.st_mtime)}"
            if seen.get(p.name) != key:
                todo.append((p, key))

    if not todo:
        print(f"[inbox] {inbox} 无新文件")
        return 0

    print(f"[inbox] 发现 {len(todo)} 个新/变更文件，开始上传：", flush=True)
    uploaded = []
    for p, key in todo:
        r = subprocess.run(
            ["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
             str(p), SERVER + ":" + REMOTE],
            capture_output=True, timeout=900)
        if r.returncode != 0:
            print(f"[inbox] ❌ 上传失败 {p.name}: "
                  f"{r.stderr.decode('utf-8', 'ignore')[:120]}")
            continue
        uploaded.append((p, key))
        print(f"[inbox] ✅ 已上传 {p.name}（{p.stat().st_size // 1024} KB）", flush=True)

    for p, key in uploaded:
        seen[p.name] = key
    SEENF.write_text(json.dumps(seen, ensure_ascii=False, indent=1), "utf-8")

    if uploaded:
        print("[inbox] 触发服务器端入库（切块 + 生成知识卡）…", flush=True)
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", SERVER,
             "cd /opt/kb && docker compose exec -T worker python scripts/ingest_docs.py"],
            capture_output=True, timeout=3600)
        out = r.stdout.decode("utf-8", "ignore")
        print(out[:800] or f"[inbox] 入库命令无输出（rc={r.returncode}）")
        print("[inbox] 完成，稍后可在知识库网页看到新条目")
    return 0


if __name__ == "__main__":
    sys.exit(main())
