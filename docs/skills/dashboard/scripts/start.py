"""
MoMo Dashboard 启动脚本
用法：python3 scripts/start.py [--username zhaili] [--port 8080]
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PYTHON = sys.executable
APP_PATH = PROJECT_ROOT / "dashboard" / "app.py"
LOG_PATH = Path("/tmp/momo_dashboard.log")


def _probe(port: int) -> dict | None:
    try:
        r = urllib.request.urlopen(f"http://localhost:{port}/api/config", timeout=2)
        return json.loads(r.read())
    except Exception:
        return None


def _fetch(port: int, path: str):
    r = urllib.request.urlopen(f"http://localhost:{port}{path}", timeout=5)
    return json.loads(r.read())


def start(username: str, port: int) -> bool:
    env = os.environ.copy()
    env["MOMO_USER"] = username
    with open(LOG_PATH, "w") as log:
        subprocess.Popen(
            [PYTHON, str(APP_PATH)],
            env=env,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    for _ in range(12):
        time.sleep(1)
        if _probe(port):
            return True
    return False


def print_summary(port: int):
    try:
        episodes = _fetch(port, "/api/episodes")
        pub_list = _fetch(port, "/api/publish")
    except Exception as e:
        print(f"[warn] 无法拉取数据：{e}")
        return

    total = len(episodes)
    completed = sum(1 for e in episodes if e["status"] == "completed")
    pending = sum(1 for e in episodes if e["status"] == "pending_verify")
    published = sum(1 for e in episodes if e["has_publish"])

    print(f"\n── Episodes ──────────────────────────────")
    print(f"总计: {total}  已完成: {completed}  待验证: {pending}  已发布: {published}")

    print(f"\n── 最近 3 条 ─────────────────────────────")
    for e in episodes[:3]:
        dur = f"{e['duration_sec']}s" if e["duration_sec"] else "-"
        plats = ",".join(e["published_platforms"]) if e["published_platforms"] else "未发布"
        inst = e["instruction"][:45]
        print(f"[{e['status']}] {inst}... ({dur}) → {plats}")

    platform_stats: dict[str, dict] = {}
    for entry in pub_list:
        for plat, data in entry.get("platforms", {}).items():
            s = data.get("stats", {})
            ps = platform_stats.setdefault(plat, {"posts": 0, "views": 0, "likes": 0})
            ps["posts"] += 1
            ps["views"] += s.get("view_count", 0)
            ps["likes"] += s.get("liked_count", 0)

    if platform_stats:
        print(f"\n── 各平台累计指标 ────────────────────────")
        for plat, s in sorted(platform_stats.items()):
            print(f"  {plat:12s}  {s['posts']} 条  播放 {s['views']:,}  点赞 {s['likes']:,}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default="zhaili")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    cfg = _probe(args.port)
    if cfg:
        print(f"[ok] 看板已在运行（用户: {cfg['username']}，端口: {args.port}）")
    else:
        print(f"[...] 启动看板（MOMO_USER={args.username} port={args.port}）...")
        if not start(args.username, args.port):
            print(f"[error] 启动失败，查看日志：cat {LOG_PATH}")
            sys.exit(1)
        print(f"[ok] 看板已启动，日志：{LOG_PATH}")

    print_summary(args.port)
    print(f"\n看板地址：http://localhost:{args.port}")


if __name__ == "__main__":
    main()
