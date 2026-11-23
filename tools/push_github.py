"""自助上传 GitHub：申请设备码 → 你在浏览器输码 → 自动推送 → 自动清理凭证。

双击同目录的 push-github.cmd 即可运行。全程看命令行窗口提示操作。
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CFGF = Path.home() / ".ai.json"
cfg = json.loads(CFGF.read_text("utf-8")) if CFGF.exists() else {}
PROXY = cfg.get("proxy", "socks5://127.0.0.1:33210")
GITHUB_USER = cfg.get("github_user", "z06125")
REPO_DIR = Path(__file__).resolve().parent.parent  # kb/ 目录
CLIENT_ID = "178c6fc778ccc68e1d6a"  # GitHub CLI 公开客户端 ID

cli = httpx.Client(proxy=PROXY, timeout=30)


def run_git(*args):
    return subprocess.run(["git", "-C", str(REPO_DIR), *args],
                          capture_output=True, text=True, encoding="utf-8")


def main():
    print("=" * 56)
    print("  自助上传 GitHub 知识库代码")
    print("=" * 56)
    status = run_git("status", "--short")
    if status.stdout.strip():
        run_git("add", "-A")
        run_git("commit", "-m", "chore: local updates " + time.strftime("%Y-%m-%d %H:%M"))
        print("已将本地改动存档")

    try:
        d = cli.post("https://github.com/login/device/code",
                     headers={"Accept": "application/json"},
                     data={"client_id": CLIENT_ID, "scope": "repo"}).json()
    except Exception as e:
        sys.exit(f"连接 GitHub 失败：{e}\n（请确认代理软件已打开）")
    if "user_code" not in d:
        sys.exit(f"申请设备码失败：{d}")

    print()
    print("  第 1 步 ▸ 用浏览器打开这个网址：")
    print(f"          {d['verification_uri']}")
    print("  第 2 步 ▸ 登录你的 GitHub（z06125）")
    print("  第 3 步 ▸ 输入这个 8 位设备码：")
    print()
    print(f"          ██████  {d['user_code']}  ██████")
    print()
    print("  第 4 步 ▸ 点绿色【Authorize】按钮授权")
    print()
    print("  （本窗口会自动等待，最长 10 分钟，授权成功即自动上传）")
    print("-" * 56)

    token = ""
    for i in range(120):
        time.sleep(5)
        r = cli.post("https://github.com/login/oauth/access_token",
                     headers={"Accept": "application/json"},
                     data={"client_id": CLIENT_ID,
                           "device_code": d["device_code"],
                           "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}).json()
        token = r.get("access_token", "")
        if token:
            break
        err = r.get("error", "")
        if err not in ("authorization_pending", "slow_down"):
            sys.exit(f"授权失败：{err} {r}")
        if i % 6 == 5:
            print(f"  等待授权中… 已等 {(i + 1) * 5} 秒")
    else:
        sys.exit("超时 10 分钟未检测到授权，请重新运行")

    print("✅ 授权成功！正在上传…")
    run_git("remote", "remove", "origin")
    run_git("remote", "add", "origin",
            f"https://{GITHUB_USER}:{token}@github.com/{GITHUB_USER}/kb-flow.git")
    out = run_git("push", "origin", "main")
    run_git("remote", "set-url", "origin",
            f"https://github.com/{GITHUB_USER}/kb-flow.git")
    if out.returncode == 0:
        print("🎉 上传完成！打开 https://github.com/z06125/kb-flow 即可看到最新版")
        print("   （访问凭证已自动清理，下次要传时再双击本工具）")
    else:
        print("上传失败：\n" + (out.stderr or out.stdout)[:600])


if __name__ == "__main__":
    main()
