# 从 skills/ 目录按任务类型加载 skill 内容，用于组装 Planner prompt。
# 通过任务判断进行 skill 导入，而非在 PLANNER_AGENT_PROMPT 里写死。

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional, Union, Dict, Tuple

import yaml

# 项目内 skills 目录（本文件在 skills/skill_loader.py，根目录为上级）
_SKILLS_DIR = Path(__file__).resolve().parent

# meta-skill: 任务编排逻辑与用户偏好，默认加载
# 公共 Plan schema 已移至 prompts.py 的 PLANNER_AGENT_PROMPT_CORE 中
META_SKILL = "META_SKILL"

# 按用户隔离：沙箱可设置此路径，使 Planner 加载对应用户的 meta-skill 文件（如 SKILL_<username>.md）
_meta_skill_path_override: Optional[Path] = None


def set_meta_skill_path(path: Optional[Union[str, Path]]) -> None:
    """设置 meta-skill 文件路径覆盖；用于按用户加载 SKILL_<username>.md。传入 None 恢复默认。"""
    global _meta_skill_path_override
    _meta_skill_path_override = Path(path).resolve() if path else None


def _read_skill_md(path: Path) -> str:
    """读取 SKILL.md，去掉 YAML frontmatter，返回 body 内容。"""
    text = path.read_text(encoding="utf-8")
    # 去掉 --- ... ---  frontmatter（仅匹配开头的成对 ---）
    if text.strip().startswith("---"):
        match = re.match(r"^---\s*\n(.*?\n)?---\s*\n", text, re.DOTALL)
        if match:
            return text[match.end() :].strip()
    return text.strip()


def _parse_frontmatter(text: str) -> Dict:
    """
    解析 SKILL.md 顶部的 YAML frontmatter，返回 dict。
    要求形如：
    ---
    name: xxx
    description: yyy
    ---
    """
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return {}
    # 匹配最前面的成对 ---\n ... \n---\n
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", stripped, re.DOTALL)
    if not m:
        return {}
    fm_text = m.group(1)
    try:
        data = yaml.safe_load(fm_text) or {}
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


@lru_cache(maxsize=1)
def _init_skills_index() -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    扫描 skills/ 下除 meta-skill 外的所有子目录，基于 SKILL.md frontmatter
    动态生成：
    - ALL_SKILLS: {KEY → 目录名}
    - SKILL_DESCRIPTIONS: {KEY → description 文本}
    
    约定：SKILL.md 的 frontmatter 至少包含：
    ---
    name: search-and-extract
    description: ...
    ---
    """
    all_skills: Dict[str, str] = {}
    descriptions: Dict[str, str] = {}

    for d in _SKILLS_DIR.iterdir():
        if not d.is_dir():
            continue
        if d.name == META_SKILL:
            # meta-skill 由专门逻辑加载，不参与 task skill 列表
            continue
        md = d / "SKILL.md"
        if not md.is_file():
            continue
        text = md.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        name = fm.get("name", d.name)
        desc = (fm.get("description") or "").strip()

        # key: 用 name 转为大写 + 下划线，保证与 prompts 中描述保持一致
        key = str(name).replace("-", "_").upper()
        all_skills[key] = d.name
        descriptions[key] = desc

    return all_skills, descriptions


# 动态初始化：ALL_SKILLS（skill_type → 目录名）与 SKILL_DESCRIPTIONS（skill_type → 描述）
ALL_SKILLS, SKILL_DESCRIPTIONS = _init_skills_index()


def load_skill(skill_name: str) -> str:
    """
    按 skill 名称加载内容。
    - meta-skill: skills/meta-skill.md（或被 override）
    - 其他 skill: skills/<skill_name>/SKILL.md
    """
    # 1）meta-skill 支持 override
    if skill_name == META_SKILL:
        if _meta_skill_path_override is not None and _meta_skill_path_override.is_file():
            return _read_skill_md(_meta_skill_path_override)
        meta_path = _SKILLS_DIR / "META_SKILL.md"
        return _read_skill_md(meta_path) if meta_path.is_file() else ""

    # 2）普通 skill: 目录 + SKILL.md
    path = _SKILLS_DIR / skill_name / "SKILL.md"
    if not path.is_file():
        return ""
    return _read_skill_md(path)


def build_available_skills_section() -> str:
    """
    构造「Available Skills」说明段落，供 prompts.py 注入到 Planner prompt 中。
    基于 SKILL_DESCRIPTIONS 动态生成，避免在 prompts.py 中手写枚举。
    """
    lines = []
    lines.append("## Available Skills:\n")
    lines.append(
        "Before calling load_skill_tool, analyze the InitUserMessage and determine which skill "
        "best matches the user's intent. All skills are unified in `ALL_SKILLS` and can be "
        "loaded via `load_skill_tool(skill_type=\"...\")`:\n"
    )

    idx = 1
    for key, desc in SKILL_DESCRIPTIONS.items():
        # desc 可能为空，此时只展示 key
        if desc:
            lines.append(f"{idx}. **{key}**: {desc}\n")
        else:
            lines.append(f"{idx}. **{key}**\n")
        idx += 1

    return "\n".join(lines).rstrip()


def infer_task_type(InitUserMessage: str) -> str:
    """
    根据用户编辑指令推断任务类型，用于决定加载哪个 task skill。
    返回 VIDEO_EXECUTION_PLAN 的 task_type 枚举之一。
    """
    if not InitUserMessage or not isinstance(InitUserMessage, str):
        return "VFX_EDIT"  # 默认
    s = InitUserMessage.strip().lower()
    # 摘要/字幕
    if any(k in s for k in ("摘要", "字幕", "summary", "caption", "subtitle")):
        return "SUMMARY"
    # 找/筛选/提取
    if any(k in s for k in ("找", "筛选", "提取", "find all", "找出", "merge into one", "合并成一个")):
        return "SEARCH_AND_EXTRACT"
    # 小说转剧本/分镜（用户明确提到小说+转剧本/分镜时）
    if any(k in s for k in ("小说", "短篇")) and any(k in s for k in ("转剧本", "转分镜", "做成剧本", "做成分镜", "生成剧本", "生成分镜", "转成剧本", "转成分镜")):
        return "NOVEL_TO_SCREENPLAY"
    # 剧本/脚本/故事 → 长视频生成（优先于 PLOT_EXTENSION 判断）
    if any(k in s for k in ("剧本", "脚本", "故事", "分镜", "screenplay", "script", "story", "剧情视频", "长视频", "根据剧本", "把故事做成视频")):
        return "LONG_VIDEO_CREATE"
    # 续写/延长/生成/短片
    if any(k in s for k in ("续写", "延长", "向前延长", "向后延长", "秒短片", "extend", "extension", "生成", "整片", "全程")):
        return "PLOT_EXTENSION"
    # 换人/换脸/风格/参考图
    return "VFX_EDIT"


def build_planner_final_section(skill_type: str | None = None) -> str:
    """
    组装 Planner 的「最终输出」整段：meta-skill + 指定的 skill。
    
    注意：公共 Plan schema 已移至 prompts.py 的 PLANNER_AGENT_PROMPT_CORE 中。
    meta-skill（任务编排逻辑与用户偏好）默认加载。
    
    Args:
        skill_type: 
            - 若传入 ALL_SKILLS 中的有效 key，只加载该 skill
            - 若为 "__NO_SKILL__"，只加载 meta-skill，不加载任何 skill
            - 若为 None，加载 meta-skill + 所有 skill
    """
    parts = []
    
    # 默认加载 meta-skill（任务编排逻辑与用户偏好）
    meta_content = load_skill(META_SKILL)
    if meta_content:
        parts.append(meta_content)
    
    if skill_type == "__NO_SKILL__":
        # 只加载 meta-skill
        pass
    elif skill_type is not None and skill_type in ALL_SKILLS:
        skill_content = load_skill(ALL_SKILLS[skill_type])
        if skill_content:
            parts.append("## Skill guidance (current: {})\n\n{}".format(skill_type, skill_content))
    else:
        # 未指定时加载全部 skill
        for skill_key, skill_dir in ALL_SKILLS.items():
            content = load_skill(skill_dir)
            if content:
                parts.append("### {} ({})\n\n{}".format(skill_key, skill_dir, content))
    return "\n\n".join(p for p in parts if p)


def list_skills() -> list[str]:
    """返回 skills/ 下所有包含 SKILL.md 的子目录名。"""
    return [
        d.name
        for d in _SKILLS_DIR.iterdir()
        if d.is_dir() and (d / "SKILL.md").is_file()
    ]
