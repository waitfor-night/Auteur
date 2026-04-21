"""
MoMo Social Media Dashboard — Flask backend
Run:  cd /home/shuyun/work/multi-shot-multi-object-long-video-edit
      .venv/bin/python dashboard/app.py
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template

app = Flask(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = PROJECT_ROOT / "workspace"

_raw_user = os.environ.get("MOMO_USER", "")
if not _raw_user:
    raise SystemExit("环境变量 MOMO_USER 未设置，请在启动前 export MOMO_USER=<username>")
# allow only safe characters — no path traversal (e.g. "../etc")
if not _raw_user.replace("-", "").replace("_", "").isalnum():
    raise SystemExit(f"MOMO_USER '{_raw_user}' contains invalid characters.")
USERNAME = _raw_user
USER_DIR = (WORKSPACE / USERNAME).resolve()
# double-check resolved path stays inside workspace/
if not str(USER_DIR).startswith(str(WORKSPACE.resolve())):
    raise SystemExit(f"MOMO_USER '{_raw_user}' resolves outside workspace.")

# sandbox-only users that have no real publish data — excluded from UI
SANDBOX_USERS = {
    "doc_rigorous", "quality_inspector", "rhythm_editor",
    "short_reversal", "storyboard_first",
}


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _ep_duration(meta: dict) -> float | None:
    s = _parse_dt(meta.get("start_time"))
    e = _parse_dt(meta.get("end_time"))
    if s and e:
        return round((e - s).total_seconds(), 1)
    return None


def _get_skill_type(history: list) -> str | None:
    if not history:
        return None
    plan = history[0].get("plan", {})
    msp = plan.get("final_multi_stage_plan") or plan.get("original_multi_stage_plan") or {}
    stages = msp.get("stages") or []
    return stages[0].get("skill_type") if stages else None


def _trim(v, max_len: int = 120) -> str:
    if v is None:
        return ""
    s = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
    return s[:max_len] + "…" if len(s) > max_len else s


def _publish_index() -> dict:
    """trace_id → publish_log_v2 entry"""
    log_path = USER_DIR / "publish_log_v2.json"
    if not log_path.exists():
        return {}
    try:
        entries = json.loads(log_path.read_text(encoding="utf-8"))
        return {e["trace_id"]: e for e in entries if "trace_id" in e}
    except Exception:
        return {}


# ── routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config")
def get_config():
    return jsonify({"username": USERNAME})


@app.route("/api/episodes")
def list_episodes():
    pub_idx = _publish_index()
    episodes = []
    pattern = str(USER_DIR / "trace" / "ep_*.json")
    for fpath in sorted(glob.glob(pattern), reverse=True):
        try:
            d = json.loads(Path(fpath).read_text(encoding="utf-8"))
        except Exception:
            continue
        ep_id = d.get("episode_id", Path(fpath).stem)
        meta = d.get("meta_info", {})
        history = d.get("history", [])
        pub = pub_idx.get(ep_id)
        episodes.append({
            "episode_id": ep_id,
            "start_time": meta.get("start_time", ""),
            "end_time": meta.get("end_time", ""),
            "duration_sec": _ep_duration(meta),
            "status": meta.get("status", "unknown"),
            "instruction": meta.get("user_instruction", "")[:120],
            "skill_type": _get_skill_type(history),
            "has_publish": pub is not None,
            "published_platforms": list(pub.get("platforms", {}).keys()) if pub else [],
        })
    episodes.sort(key=lambda x: x["start_time"] or "", reverse=True)
    return jsonify(episodes)


@app.route("/api/episode/<ep_id>")
def get_episode(ep_id: str):
    pub_idx = _publish_index()
    fpath = USER_DIR / "trace" / f"{ep_id}.json"
    if not fpath.exists():
        return jsonify({"error": "not found"}), 404

    d = json.loads(fpath.read_text(encoding="utf-8"))
    meta = d.get("meta_info", {})
    history = d.get("history", [])
    ep_start = _parse_dt(meta.get("start_time"))

    # ── tool calls with timeline offsets ──
    tool_calls = []
    for h in history:
        for tc in h.get("tool_calls", []):
            ts = _parse_dt(tc.get("timestamp"))
            offset = round((ts - ep_start).total_seconds(), 3) if (ts and ep_start) else 0
            # compact kwargs summary
            inputs = tc.get("inputs") or {}
            kwargs = inputs.get("kwargs", {}) if isinstance(inputs, dict) else {}
            kw_parts = [f"{k}={_trim(v, 60)}" for k, v in list(kwargs.items())[:4]]
            tool_calls.append({
                "tool_name": tc.get("tool_name", ""),
                "timestamp": tc.get("timestamp", ""),
                "offset_sec": offset,
                "duration": tc.get("duration") or 0,
                "status": tc.get("status", ""),
                "inputs_summary": ", ".join(kw_parts)[:140],
                "outputs_summary": _trim(tc.get("outputs"), 200),
                "error_msg": tc.get("error_msg"),
            })

    # ── stage plan ──
    stages = []
    if history:
        plan = history[0].get("plan", {})
        msp = (
            plan.get("final_multi_stage_plan")
            or plan.get("original_multi_stage_plan")
            or {}
        )
        for s in msp.get("stages") or []:
            stages.append({
                "stage_id": s.get("stage_id"),
                "stage_name": s.get("stage_name"),
                "skill_type": s.get("skill_type"),
                "status": s.get("status"),
            })

    return jsonify({
        "episode_id": ep_id,
        "meta_info": {
            "instruction": meta.get("user_instruction", ""),
            "status": meta.get("status", ""),
            "start_time": meta.get("start_time", ""),
            "end_time": meta.get("end_time", ""),
            "duration_sec": _ep_duration(meta),
            "final_output": meta.get("final_output", ""),
            "time_length": meta.get("time_length"),
        },
        "stages": stages,
        "tool_calls": tool_calls,
        "publish_data": pub_idx.get(ep_id),
    })


@app.route("/api/publish")
def get_publish():
    log_path = USER_DIR / "publish_log_v2.json"
    if not log_path.exists():
        return jsonify([])
    try:
        return jsonify(json.loads(log_path.read_text(encoding="utf-8")))
    except Exception:
        return jsonify([])


if __name__ == "__main__":
    app.run(debug=True, port=8080, host="0.0.0.0")
