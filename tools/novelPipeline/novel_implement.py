"""
Novel-to-screenplay pipeline tools. Call LLM via OpenAI/Ark (same as mmut_implement).
Each tool reads from path(s), calls LLM, writes result files, returns paths + summary.
"""
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from agno.tools import tool
from openai import OpenAI
from utils.trace_recorder import recorder

_ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
_MODEL = "doubao-seed-1-8-251228"


def _call_llm(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4000,
    temperature: float = 0.3,
) -> str:
    """Call Ark LLM (text-in text-out)."""
    client = OpenAI(base_url=_ARK_BASE, api_key=os.environ.get("ARK_API_KEY"))
    resp = client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


def _extract_json(text: str) -> Optional[str]:
    """Extract JSON array or object from model response."""
    if not text:
        return None
    # Try to find ```json ... ``` first
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        return m.group(1).strip()
    # Then try raw { ... } or [ ... ]
    m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if m:
        return m.group(1)
    return None


def _resolve_path(path: str, base: Optional[Path] = None) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = (base or Path.cwd()).resolve() / p
    return p.resolve()


def _read_text(path: Path, max_len: int = 5000) -> str:
    return path.read_text(encoding="utf-8", errors="replace")[:max_len]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---- Stage 1: analyze (characters + locations) ----
def _run_analyze_characters(text: str) -> list:
    system = """你是一个专业的小说分析助手。请从小说文本中提取所有角色信息。
输出格式必须是 JSON 数组，每个角色包含以下字段：
- name: 角色名（必填）
- introduction: 角色简介（外貌、身份、性格等，100字以内）
- role_level: 角色重要性（主角/配角/龙套）
- personality_tags: 性格标签数组（3-5个）
- visual_keywords: 视觉关键词数组（3-5个，用于图像生成）
只输出 JSON，不要其他解释，不要 markdown 格式。"""
    user = f"请分析以下小说文本，提取所有角色信息：\n\n---\n{text}\n---\n\n请以 JSON 格式输出角色列表。"
    raw = _call_llm(system, user, max_tokens=2000)
    js = _extract_json(raw)
    if not js:
        raise ValueError("未从响应中提取到 JSON 数据")
    data = json.loads(js)
    if not isinstance(data, list):
        data = [data]
    return data


def _run_analyze_locations(text: str) -> list:
    system = """你是一个专业的小说分析助手。请从小说文本中提取所有场景/地点信息。
输出格式必须是 JSON 数组，每个场景包含以下字段：
- name: 场景名（必填，简洁的地点名称，2-6字）
- description: 场景描述（环境、氛围等，100字以内）
- summary: 场景摘要（20字以内）
- int_ext: 内/外/内外的
只输出 JSON，不要其他解释，不要 markdown 格式。"""
    user = f"请分析以下小说文本，提取所有场景/地点信息：\n\n---\n{text}\n---\n\n请以 JSON 格式输出场景列表。"
    raw = _call_llm(system, user, max_tokens=2000)
    js = _extract_json(raw)
    if not js:
        raise ValueError("未从响应中提取到 JSON 数据")
    data = json.loads(js)
    if not isinstance(data, list):
        data = [data]
    return data


def _duration_hint_to_sec(h: Any) -> float:
    if h is None or h == "":
        return 4.0
    m = re.search(r"(\d+(?:\.\d+)?)", str(h))
    return float(m.group(1)) if m else 4.0


def _normalize_scene_dialogues(scene: dict) -> None:
    for d in scene.get("dialogues") or []:
        if not isinstance(d, dict):
            continue
        lines = d.get("lines")
        if isinstance(lines, str):
            d["lines"] = [lines]
        elif lines is None:
            d["lines"] = []
        elif not isinstance(lines, list):
            d["lines"] = [str(lines)]


def _renumber_beats(beats: List[dict]) -> List[dict]:
    out = []
    for i, b in enumerate(beats, 1):
        bb = dict(b) if isinstance(b, dict) else {}
        bb["beat_id"] = i
        out.append(bb)
    return out


def _expand_to_video_units(scene: dict) -> dict:
    """将顶层 narrative_beats 按约15s一组拆成 video_units。"""
    beats = scene.get("narrative_beats") or []
    if not beats:
        scene["video_units"] = [{
            "unit_id": 1,
            "narrative_beats": _renumber_beats([{
                "beat_id": 1,
                "visual": "（待补全）",
                "audio": "无对白",
                "character": "",
                "parenthetical": "",
                "duration_hint": "15s",
            }]),
        }]
        return scene
    units: List[dict] = []
    chunk: List[dict] = []
    acc = 0.0
    uid = 1
    for b in beats:
        bd = dict(b) if isinstance(b, dict) else {}
        sec = _duration_hint_to_sec(bd.get("duration_hint"))
        if chunk and (acc + sec > 16 or len(chunk) >= 6):
            units.append({"unit_id": uid, "narrative_beats": _renumber_beats(chunk)})
            uid += 1
            chunk = []
            acc = 0.0
        chunk.append(bd)
        acc += sec
    if chunk:
        units.append({"unit_id": uid, "narrative_beats": _renumber_beats(chunk)})
    scene = dict(scene)
    scene["video_units"] = units
    return scene


def _flatten_video_units_to_narrative_beats(scene: dict) -> dict:
    """从 video_units 生成顶层 narrative_beats（连续 beat_id）。"""
    flat: List[dict] = []
    n = 1
    for u in scene.get("video_units") or []:
        for b in u.get("narrative_beats") or []:
            bb = dict(b)
            bb["beat_id"] = n
            n += 1
            flat.append(bb)
    scene = dict(scene)
    scene["narrative_beats"] = flat
    return scene


def _finalize_clip_scene(data: dict) -> dict:
    """归一化 dialogues；保证 video_units；顶层 narrative_beats 扁平化。"""
    data = dict(data)
    _normalize_scene_dialogues(data)
    vus = data.get("video_units")
    if isinstance(vus, list) and len(vus) > 0:
        fixed_units = []
        for u in vus:
            uu = dict(u) if isinstance(u, dict) else {}
            beats = uu.get("narrative_beats") or []
            if not beats:
                beats = [{
                    "beat_id": 1,
                    "visual": "（见 action_lines）",
                    "audio": "无对白",
                    "character": "",
                    "parenthetical": "",
                    "duration_hint": "15s",
                }]
            uu["unit_id"] = uu.get("unit_id") or len(fixed_units) + 1
            uu["narrative_beats"] = _renumber_beats([dict(b) for b in beats])
            fixed_units.append(uu)
        for i, uu in enumerate(fixed_units, 1):
            uu["unit_id"] = i
        data["video_units"] = fixed_units
        data = _flatten_video_units_to_narrative_beats(data)
    else:
        data = _ensure_narrative_beats(data)
        data = _expand_to_video_units(data)
        data = _flatten_video_units_to_narrative_beats(data)
    return data


# ---- Stage 2a: split clips ----
def _run_split_clips(text: str, characters: List[str], locations: List[str]) -> list:
    system = """你是一个专业的小说分析助手。请将小说文本切分为可拍摄的叙事片段。
原则：**一个场景 = 一个片段（一个 clip）**。当地点/时间/叙事空间发生明确切换时必须开启新片段；同一时空内连续动作与对话应合并在同一片段。
注意：后续步骤会在每个 clip 内再灵活拆成多个约 15 秒 video_units，因此此处不要按 15 秒切碎，但也不能把多个场景合成一个片段。
每个片段应包含：
- start: 片段起始文本（原文中的前20-30字，必须完全匹配原文）
- end: 片段结束文本（原文中的后20-30字，必须完全匹配原文）
- summary: 片段摘要（50字以内）
- location: 场景名（**只能选一个**，必须从提供的场景列表中选择，禁止用逗号拼接多个场景）
- characters: 涉及角色列表（必须是数组，元素必须从提供的角色列表中选择）
输出格式必须是 JSON 数组。只输出 JSON，不要其他解释，不要 markdown 格式。"""
    user = f"""请将以下小说文本按「场景」切分为叙事片段（一个场景一段）。段数应能覆盖文本中显著的场景切换，不要合并多个场景为一段。
已知角色: {', '.join(characters)}
已知场景: {', '.join(locations)}

小说文本：
---
{text}
---
请以 JSON 格式输出片段列表。"""
    raw = _call_llm(system, user, max_tokens=6000)
    js = _extract_json(raw)
    if not js:
        raise ValueError("未从响应中提取到 JSON 数据")
    data = json.loads(js)
    if not isinstance(data, list):
        data = [data]
    return data


# ---- Stage 2b: convert one clip to screenplay ----
def _ensure_narrative_beats(scene: dict) -> dict:
    """若模型未返回 narrative_beats，从 shots/dialogues 兜底生成，避免下游断裂。"""
    scene = dict(scene)
    _normalize_scene_dialogues(scene)
    beats = scene.get("narrative_beats")
    if isinstance(beats, list) and len(beats) > 0:
        return scene
    shots = scene.get("shots") or []
    dialogues = scene.get("dialogues") or []
    new_beats: List[dict] = []
    if shots:
        di = 0
        for i, sh in enumerate(shots, 1):
            desc = sh.get("description") or sh.get("shot_type") or ""
            cam = sh.get("camera_move") or ""
            visual = f"{sh.get('shot_type', '')} {desc} {cam}".strip()
            audio = "无对白"
            character = ""
            parenthetical = ""
            if di < len(dialogues):
                d = dialogues[di]
                character = d.get("character") or ""
                lines = d.get("lines") or []
                parenthetical = d.get("parenthetical") or ""
                audio = " / ".join(str(x) for x in lines) if lines else "无对白"
                di += 1
            new_beats.append({
                "beat_id": i,
                "visual": visual,
                "audio": audio,
                "character": character,
                "parenthetical": parenthetical,
                "duration_hint": str(sh.get("duration") or ""),
            })
    else:
        for j, d in enumerate(dialogues, 1):
            lines = d.get("lines") or []
            new_beats.append({
                "beat_id": j,
                "visual": "（见 action_lines）",
                "audio": " / ".join(str(x) for x in lines) if lines else "无对白",
                "character": d.get("character") or "",
                "parenthetical": d.get("parenthetical") or "",
                "duration_hint": "",
            })
        if not new_beats and scene.get("action_lines"):
            new_beats.append({
                "beat_id": 1,
                "visual": "\n".join(scene.get("action_lines") or []),
                "audio": "无对白",
                "character": "",
                "parenthetical": "",
                "duration_hint": "15s",
            })
    if not new_beats:
        new_beats.append({
            "beat_id": 1,
            "visual": "（请根据原文补全画面）",
            "audio": "无对白",
            "character": "",
            "parenthetical": "",
            "duration_hint": "15s",
        })
    scene["narrative_beats"] = new_beats
    return scene


def _run_convert_clip(content: str, characters: List[str], locations: List[str]) -> dict:
    system = """你是一个专业的剧本编剧。请将小说片段转换为电影剧本 JSON。

【重要】本片段可能很长，对应**多场戏、多句对白**。严禁删减、合并原文中的对白与旁白。
- dialogues: **必填**，列出片段内**每一句**对白（含引号内原文）；每条 character + lines 为字符串数组（一句一行）、parenthetical 可选。
- video_units: **必填**，按叙事时间顺序切分；**每个 unit 对应约12-15秒成片**（下游一条约15秒视频）。
  每个元素: unit_id (从1递增), narrative_beats (数组)
- 每个 beat: beat_id (unit 内从1递增), visual (画面/景别/运动), audio (该拍台词或「无对白」), character/parenthetical/duration_hint 可选；unit 内各 beat 的 duration_hint 之和建议约12-18秒。
- **所有 dialogues 中的台词必须出现在某一 unit 的某一 beat 的 audio 中**（不可遗漏）。

【保留】heading, time, int_ext, location, action_lines（动作概括）, shots（可选）

只输出 JSON，不要 markdown。"""
    user = f"""请将以下片段转为剧本 JSON。必须完整保留全部对白到 dialogues 与 video_units 的 beats 中。
已知角色: {', '.join(characters)}
已知场景: {', '.join(locations)}

片段原文：
---
{content}
---
请以 JSON 输出。"""
    raw = _call_llm(system, user, max_tokens=12000)
    js = _extract_json(raw)
    if not js:
        raise ValueError("未从响应中提取到 JSON 数据")
    data = json.loads(js)
    return _finalize_clip_scene(data)


def _beats_list_to_text(beats: List[dict]) -> str:
    parts = []
    for b in beats:
        bid = b.get("beat_id", "")
        vis = str(b.get("visual") or "").strip()
        aud = str(b.get("audio") or "").strip()
        ch = str(b.get("character") or "").strip()
        par = str(b.get("parenthetical") or "").strip()
        dh_raw = b.get("duration_hint")
        dh = "" if dh_raw is None else str(dh_raw).strip()
        line = f"[Beat {bid}]"
        if dh:
            line += f" ({dh})"
        line += f"\n画面: {vis}\n声画: "
        if ch:
            line += f"{ch}"
            if par:
                line += f" {par}: "
        line += aud
        parts.append(line)
    return "\n\n".join(parts) if parts else "（无 beats）"


def _beats_to_scene_text(scene: dict) -> str:
    beats = scene.get("narrative_beats")
    if isinstance(beats, list) and len(beats) > 0:
        return _beats_list_to_text(beats)
    action_lines = scene.get("action_lines") or []
    dialogues = scene.get("dialogues") or []
    dlg_lines = []
    for d in dialogues:
        lines = d.get("lines") or []
        if isinstance(lines, str):
            t = lines
        else:
            t = " / ".join(str(x) for x in lines)
        dlg_lines.append(f"{d.get('character', '')}: {t}")
    dlg = "\n".join(dlg_lines)
    return f"""动作描述:
{chr(10).join(str(x) for x in action_lines)}

对话:
{dlg}"""


def _get_video_units_for_scene(scene: dict) -> List[dict]:
    """从已合并 scene 取 video_units；旧数据无则现场展开。"""
    s = dict(scene)
    _normalize_scene_dialogues(s)
    vus = s.get("video_units")
    if isinstance(vus, list) and len(vus) > 0:
        return vus
    if s.get("narrative_beats"):
        s = _expand_to_video_units(s)
        return s.get("video_units") or []
    s = _ensure_narrative_beats(s)
    s = _expand_to_video_units(s)
    return s.get("video_units") or [{"unit_id": 1, "narrative_beats": [{"beat_id": 1, "visual": "…", "audio": "无对白"}]}]


# ---- Stage 3: script to storyboard ----
def _merge_panels_to_one(panels: List[dict], scene_idx: int) -> dict:
    """多 panel 时合并为单条 15s 分镜。"""
    contents = []
    for p in panels:
        c = (p.get("content") or "").strip()
        if c:
            contents.append(c)
    merged_content = "\n\n".join(contents) if contents else "（无画面描述）"
    return {
        "shot_number": 1,
        "shot_type": "15s内多镜头综合",
        "camera_move": "按 content 内时间轴执行",
        "content": merged_content,
        "dialogue": panels[0].get("dialogue") if panels else "",
        "duration": 15,
        "notes": panels[0].get("notes") if panels else "",
    }


def _run_scene_to_storyboard(
    scene_heading: str,
    scene_body_text: str,
    characters: List[str],
    locations: List[str],
    style: str,
) -> list:
    system = f"""你是专业的影视分镜师。根据下方「叙事单元（画面+声画）」生成**一条**拍摄分镜（单片段约15秒成片）。
分镜风格: {style}

【输出】必须是 JSON 数组，且**长度恰好为 1**。唯一元素字段：
- shot_number: 1
- shot_type: 可用「15s内多镜头综合」
- camera_move: 概括或写「见 content」
- content: **必须**严格包含三段，每段综合画面与台词：
  【0-5s】... 【5-10s】... 【10-15s】...
  每段写明构图、光线、人物、镜头感及该时段对白/旁白。
- dialogue: 可选，代表性台词摘要
- duration: 整数 15
- notes: 音效/BGM/调色等

只输出 JSON 数组，不要 markdown。"""
    scene_content = f"""场景标题: {scene_heading}

叙事单元（画面与声画已对齐）:
{scene_body_text}"""
    user = f"""请为以下场景生成**一条**15秒分镜（content 内必须含【0-5s】【5-10s】【10-15s】）：

{scene_content}

角色列表: {', '.join(characters)}
场景列表: {', '.join(locations)}

请以 JSON 数组输出，且仅含 1 个对象。"""
    raw = _call_llm(system, user, max_tokens=3500)
    js = _extract_json(raw)
    if not js:
        return []
    panels = json.loads(js)
    if not isinstance(panels, list):
        panels = [panels]
    if len(panels) > 1:
        panels = [_merge_panels_to_one(panels, 0)]
    elif len(panels) == 1:
        p = panels[0]
        p["duration"] = 15
        if not p.get("shot_type"):
            p["shot_type"] = "15s内多镜头综合"
    return panels


# ========== Tools (agno @tool) ==========

@tool
@recorder.record
def novel_analyze_tool(novel_path: str, output_dir: str) -> dict:
    """
    分析小说中的角色与场景，产出 CP1：cp1_characters.json、cp1_locations.json。
    novel_path 与 output_dir 可为相对路径（基于当前工作目录）。
    返回路径与摘要，供下一步使用。
    """
    base = Path.cwd().resolve()
    npath = _resolve_path(novel_path, base)
    odir = _resolve_path(output_dir, base)
    odir.mkdir(parents=True, exist_ok=True)
    try:
        text = _read_text(npath, 5000)
        chars = _run_analyze_characters(text)
        locs = _run_analyze_locations(text)
        cp1_c = odir / "cp1_characters.json"
        cp1_l = odir / "cp1_locations.json"
        _write_json(cp1_c, {
            "cp_id": "CP1", "cp_name": "characters", "version": "1.0",
            "data": chars, "status": "completed",
        })
        _write_json(cp1_l, {
            "cp_id": "CP1", "cp_name": "locations", "version": "1.0",
            "data": locs, "status": "completed",
        })
        def _rel(p: Path) -> str:
            try:
                return str(p.relative_to(base))
            except ValueError:
                return str(p)
        return {
            "cp1_characters_path": _rel(cp1_c),
            "cp1_locations_path": _rel(cp1_l),
            "output_files": [_rel(cp1_c), _rel(cp1_l)],
            "summary": f"已分析 {len(chars)} 个角色、{len(locs)} 个场景",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@tool
@recorder.record
def novel_split_clips_tool(
    novel_path: str,
    cp1_characters_path: str,
    cp1_locations_path: str,
    output_dir: str,
) -> dict:
    """
    将小说切分为叙事大段（少而完整；段内在 CP3 再拆为多个约15s 视频单元），产出 cp2_clips.json。
    """
    base = Path.cwd().resolve()
    npath = _resolve_path(novel_path, base)
    odir = _resolve_path(output_dir, base)
    c1 = _read_json(_resolve_path(cp1_characters_path, base))
    l1 = _read_json(_resolve_path(cp1_locations_path, base))
    chars = [x["name"] for x in c1.get("data", [])]
    locs = [x["name"] for x in l1.get("data", [])]
    odir.mkdir(parents=True, exist_ok=True)
    try:
        text = _read_text(npath, 32000)
        items = _run_split_clips(text, chars, locs)
        clips = []
        for i, item in enumerate(items, 1):
            start = item.get("start", "")
            end = item.get("end", "")
            start_idx = text.find(start)
            end_idx = text.find(end)
            if start_idx >= 0 and end_idx >= 0:
                content = text[start_idx : end_idx + len(end)]
            else:
                content = start + "..." + end
            # LLM 偶发把 list 写成 str，这里做最小兜底规范化
            loc = item.get("location", "某处")
            if isinstance(loc, str) and any(sep in loc for sep in [",", "，", "、", ";", "；", "|"]):
                loc = re.split(r"[,，、;；|]+", loc)[0].strip() or loc
            chs = item.get("characters", [])
            if isinstance(chs, str):
                parts = [p.strip() for p in re.split(r"[,，、;；|]+", chs) if p.strip()]
                chs = parts
            elif chs is None:
                chs = []
            elif not isinstance(chs, list):
                chs = [str(chs)]
            clips.append({
                "clip_id": f"clip_{i:02d}",
                "start_text": start,
                "end_text": end,
                "content": content,
                "summary": item.get("summary", ""),
                "location": loc,
                "characters": chs,
            })
        out = odir / "cp2_clips.json"
        _write_json(out, {
            "cp_id": "CP2", "cp_name": "clipList", "version": "1.0",
            "data": clips, "status": "completed",
        })
        def _rel(p: Path) -> str:
            try:
                return str(p.relative_to(base))
            except ValueError:
                return str(p)
        return {
            "cp2_clips_path": _rel(out),
            "output_files": [_rel(out)],
            "summary": f"已切分为 {len(clips)} 个片段",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@tool
@recorder.record
def novel_convert_clips_tool(
    cp2_clips_path: str,
    cp1_characters_path: str,
    cp1_locations_path: str,
    output_dir: str,
) -> dict:
    """
    片段转剧本：含 video_units（每 unit 约15s 成片）、完整 dialogues、narrative_beats 扁平备份。
    """
    base = Path.cwd().resolve()
    odir = _resolve_path(output_dir, base)
    cp3_dir = odir / "cp3_clips"
    cp3_dir.mkdir(parents=True, exist_ok=True)
    cp2 = _read_json(_resolve_path(cp2_clips_path, base))
    c1 = _read_json(_resolve_path(cp1_characters_path, base))
    l1 = _read_json(_resolve_path(cp1_locations_path, base))
    chars = [x["name"] for x in c1.get("data", [])]
    locs = [x["name"] for x in l1.get("data", [])]
    clips = cp2.get("data", [])
    output_file_list = []
    try:
        def _rel(p: Path) -> str:
            try:
                return str(p.relative_to(base))
            except ValueError:
                return str(p)
        for i, clip in enumerate(clips, 1):
            clip_id = clip.get("clip_id", f"clip_{i:02d}")
            scene_data = _run_convert_clip(clip.get("content", ""), chars, locs)
            out = cp3_dir / f"cp3_{clip_id}.json"
            _write_json(out, {
                "cp_id": "CP3", "cp_name": "screenplayResult", "version": "1.0",
                "clip_id": clip_id,
                "data": scene_data,
                "status": "completed",
            })
            output_file_list.append(_rel(out))
        return {
            "cp3_clips_dir": _rel(cp3_dir),
            "output_files": output_file_list,
            "summary": f"已转换 {len(clips)} 个片段为剧本",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@tool
@recorder.record
def novel_merge_screenplay_tool(
    cp3_clips_dir: str,
    cp1_characters_path: str,
    cp1_locations_path: str,
    output_dir: str,
    title: str = "未命名剧本",
) -> dict:
    """
    合并所有剧本片段为完整剧本，产出 cp2_full_screenplay.txt 与 cp3_merged_results.json。
    """
    base = Path.cwd().resolve()
    odir = _resolve_path(output_dir, base)
    clips_dir = _resolve_path(cp3_clips_dir, base)
    c1 = _read_json(_resolve_path(cp1_characters_path, base))
    l1 = _read_json(_resolve_path(cp1_locations_path, base))
    odir.mkdir(parents=True, exist_ok=True)
    try:
        clip_files = sorted(clips_dir.glob("cp3_clip_*.json"))
        scenes = []
        for f in clip_files:
            data = _read_json(f)
            scenes.append(data["data"])
        for i, s in enumerate(scenes, 1):
            s["scene_number"] = i
            s["transition"] = "切至:" if i < len(scenes) else ""
        merged = {
            "cp_id": "CP3", "cp_name": "mergedScreenplayResults", "version": "1.0",
            "title": title,
            "characters": c1.get("data", []),
            "locations": l1.get("data", []),
            "scenes": scenes,
            "status": "completed",
        }
        json_path = odir / "cp3_merged_results.json"
        _write_json(json_path, merged)
        # Simple screenplay text
        lines = [title.center(60), "=" * 60, ""]
        for s in scenes:
            lines.append("")
            lines.append((s.get("heading") or "内 某处 - 日").upper().center(60))
            lines.append("-" * 60)
            _normalize_scene_dialogues(s)
            vus = s.get("video_units")
            if isinstance(vus, list) and len(vus) > 0:
                lines.append("    [对白清单]")
                for d in s.get("dialogues") or []:
                    ch = d.get("character") or "角色"
                    for ln in (d.get("lines") or []):
                        lines.append(f"    {ch}: {ln}")
                lines.append("")
                for u in vus:
                    uid = u.get("unit_id", "")
                    lines.append(f"=== Unit {uid} (约15s) ===")
                    for b in u.get("narrative_beats") or []:
                        bid = b.get("beat_id", "")
                        lines.append(f"    --- Beat {bid} ---")
                        lines.append("    [画面] " + str(b.get("visual") or "").strip())
                        ch = (b.get("character") or "").strip()
                        aud = str(b.get("audio") or "").strip()
                        par = (b.get("parenthetical") or "").strip()
                        if ch or par:
                            lines.append(f"    [声画] {ch} {par}: {aud}".strip())
                        else:
                            lines.append(f"    [声画] {aud}")
                    lines.append("")
            else:
                nb = s.get("narrative_beats")
                if isinstance(nb, list) and len(nb) > 0:
                    for b in nb:
                        bid = b.get("beat_id", "")
                        lines.append(f"--- Beat {bid} ---")
                        lines.append("    [画面] " + str(b.get("visual") or "").strip())
                        ch = (b.get("character") or "").strip()
                        aud = str(b.get("audio") or "").strip()
                        par = (b.get("parenthetical") or "").strip()
                        if ch or par:
                            lines.append(f"    [声画] {ch} {par}: {aud}".strip())
                        else:
                            lines.append(f"    [声画] {aud}")
                        lines.append("")
                else:
                    for a in s.get("action_lines", []):
                        if str(a).strip():
                            lines.append("    " + str(a).strip())
                    for d in s.get("dialogues", []):
                        lines.append((d.get("character") or "角色").upper().center(40))
                        for line in (d.get("lines") or []):
                            lines.append(str(line).center(50))
            lines.append("")
        lines.append("剧终".center(60))
        txt_path = odir / "cp2_full_screenplay.txt"
        txt_path.write_text("\n".join(lines), encoding="utf-8")
        def _rel(p: Path) -> str:
            try:
                return str(p.relative_to(base))
            except ValueError:
                return str(p)
        return {
            "cp2_full_screenplay_path": _rel(txt_path),
            "cp3_merged_path": _rel(json_path),
            "output_files": [_rel(txt_path), _rel(json_path)],
            "summary": f"已合并 {len(scenes)} 个场景",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@tool
@recorder.record
def novel_script_to_storyboard_tool(
    cp3_merged_path: str,
    output_dir: str,
    title: str = "未命名剧本",
    storyboard_style: str = "cinematic",
) -> dict:
    """
    剧本转分镜：每场景内按 video_units 各生成 1 个 panel（每条约 15s），content 含【0-5s】【5-10s】【10-15s】。
    panel 带 video_unit_id；对白完整依赖 CP3 的 dialogues / beats。
    """
    base = Path.cwd().resolve()
    odir = _resolve_path(output_dir, base)
    cp3 = _read_json(_resolve_path(cp3_merged_path, base))
    scenes = cp3.get("scenes", [])
    characters = cp3.get("characters", [])
    locations = cp3.get("locations", [])
    char_names = [c.get("name", "") for c in characters] if characters and isinstance(characters[0], dict) else []
    loc_names = [l.get("name", "") for l in locations] if locations and isinstance(locations[0], dict) else []
    odir.mkdir(parents=True, exist_ok=True)
    try:
        all_panels = []
        current_panel_id = 1
        for scene_idx, scene in enumerate(scenes, 1):
            heading = scene.get("heading", "内 某处 - 日")
            units = _get_video_units_for_scene(scene)
            for unit in units:
                uid = unit.get("unit_id", 0)
                beats = unit.get("narrative_beats") or []
                body = _beats_list_to_text(beats)
                panels = _run_scene_to_storyboard(
                    heading, body, char_names, loc_names, storyboard_style,
                )
                if not panels:
                    panels = [{
                        "shot_number": 1,
                        "shot_type": "15s内多镜头综合",
                        "camera_move": "固定",
                        "content": f"【0-5s】{body[:400]}...\n【5-10s】（承接叙事）\n【10-15s】（收束）",
                        "dialogue": "",
                        "duration": 15,
                        "notes": "LLM 分镜失败，请人工补全",
                    }]
                if len(panels) > 1:
                    panels = [_merge_panels_to_one(panels, scene_idx)]
                for p in panels:
                    p["panel_id"] = current_panel_id
                    p["scene_id"] = scene_idx
                    p["video_unit_id"] = uid
                    p["duration"] = 15
                    all_panels.append(p)
                    current_panel_id += 1
        cp4_path = odir / "cp4_plan_panels.json"
        cp5_path = odir / "cp5_storyboards.json"
        _write_json(cp4_path, {
            "cp_id": "CP4", "cp_name": "planPanels", "version": "1.0",
            "title": title, "style": storyboard_style, "total_panels": len(all_panels),
            "data": [{
                "panel_id": p["panel_id"],
                "scene_id": p["scene_id"],
                "video_unit_id": p.get("video_unit_id"),
                "shot_type": p.get("shot_type", ""),
                "duration": p.get("duration", 15),
            } for p in all_panels],
            "status": "completed",
        })
        _write_json(cp5_path, {
            "cp_id": "CP5", "cp_name": "storyboards", "version": "1.0",
            "title": title, "style": storyboard_style, "total_panels": len(all_panels),
            "characters": characters, "locations": locations, "scenes": scenes, "panels": all_panels,
            "status": "completed",
        })
        def _rel(p: Path) -> str:
            try:
                return str(p.relative_to(base))
            except ValueError:
                return str(p)
        return {
            "cp4_plan_path": _rel(cp4_path),
            "cp5_storyboards_path": _rel(cp5_path),
            "output_files": [_rel(cp4_path), _rel(cp5_path)],
            "summary": f"已生成 {len(all_panels)} 个分镜",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
