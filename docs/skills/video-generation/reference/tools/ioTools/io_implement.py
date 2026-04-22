"""
read_tool / write_tool: 文件读写工具。
- read_tool: 可读取当前工作目录及「允许读取的根目录」下的文件（含项目根、数据文件夹等），便于 Planner 读取数据目录下的小说等。
- write_tool: 仅允许写入当前工作目录（output_dir）下的路径。
返回 path + 可选 content/summary，便于路径交互与上下文控制。
"""
import json
import os
from pathlib import Path
from typing import Any, List, Optional

from agno.tools import tool
from utils.trace_recorder import recorder

# 项目根目录（tools/ioTools/__file__ 的上两级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _get_read_allowed_bases() -> List[Path]:
    """read_tool 允许读取的根目录列表：当前工作目录、项目根、以及环境变量 READ_ALLOWED_BASES 中的路径。"""
    bases = [Path.cwd().resolve(), _PROJECT_ROOT.resolve()]
    extra = os.environ.get("READ_ALLOWED_BASES", "").strip()
    if extra:
        for part in extra.split(","):
            part = part.strip()
            if part:
                bases.append(Path(part).resolve())
    return bases


def _path_under_any_base(p: Path, bases: List[Path]) -> bool:
    """判断 p 是否位于 bases 中某一目录下（或等于该目录）。"""
    p = p.resolve()
    for base in bases:
        base = base.resolve()
        try:
            if p == base or p.is_relative_to(base):
                return True
        except AttributeError:
            if p == base or str(p).startswith(str(base) + os.sep):
                return True
    return False


def _resolve_and_validate_path_for_read(path: str) -> Path:
    """解析路径并校验：必须在任一「允许读取的根目录」下，且文件存在。用于 read_tool。"""
    p = Path(path)
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    else:
        p = p.resolve()
    bases = _get_read_allowed_bases()
    if not _path_under_any_base(p, bases):
        raise ValueError(
            f"读取路径必须在允许的根目录之一下: {[str(b) for b in bases]}，得到: {p}"
        )
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")
    if not p.is_file():
        raise ValueError(f"路径不是文件: {p}")
    return p


def _resolve_and_validate_path(path: str, allow_write: bool = False) -> Path:
    """解析路径并校验在允许根目录下。允许根目录为当前工作目录。仅用于 write_tool 及内部写操作。"""
    p = Path(path).resolve()
    base = Path.cwd().resolve()
    try:
        if not p.is_relative_to(base):
            raise ValueError(f"路径必须在当前工作目录下: {base}，得到: {p}")
    except AttributeError:
        # Python < 3.9
        if not str(p).startswith(str(base)):
            raise ValueError(f"路径必须在当前工作目录下: {base}，得到: {p}")
    if allow_write:
        p.parent.mkdir(parents=True, exist_ok=True)
    elif not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")
    return p


def _make_summary_for_json(content: str) -> Optional[str]:
    """对 JSON 内容生成简短摘要。"""
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            if "data" in data and isinstance(data["data"], list):
                return f"共 {len(data['data'])} 条"
            if "panels" in data and isinstance(data["panels"], list):
                return f"共 {len(data['panels'])} 个分镜"
            if "scenes" in data and isinstance(data["scenes"], list):
                return f"共 {len(data['scenes'])} 个场景"
        if isinstance(data, list):
            return f"共 {len(data)} 条"
    except Exception:
        pass
    return None


@tool
@recorder.record
def read_tool(path: str, max_chars: Optional[int] = None) -> dict:
    """
    读取文本或 JSON 文件，返回内容与可选摘要。
    可读取：当前工作目录、项目根目录、以及环境变量 READ_ALLOWED_BASES 中配置的目录下的文件
    （便于 Planner 读取数据文件夹中的小说等）；相对路径先按当前工作目录解析，也可传绝对路径。

    Args:
        path: 文件路径（.txt 或 .json），可为相对路径或绝对路径。
        max_chars: 可选，最大返回字符数；超出则截断并在返回中注明。

    Returns:
        {"path": str, "content": str, "summary": str | null}
        content 可能被截断；summary 对 JSON 可生成简短描述（如「共 N 条」）。
    """
    try:
        p = _resolve_and_validate_path_for_read(path)
    except (ValueError, FileNotFoundError) as e:
        return {"path": path, "content": "", "summary": None, "error": str(e)}

    if p.suffix.lower() not in (".txt", ".json"):
        return {"path": str(p), "content": "", "summary": None, "error": "仅支持 .txt 或 .json 文件"}

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"path": str(p), "content": "", "summary": None, "error": str(e)}

    truncated = False
    if max_chars is not None and len(content) > max_chars:
        content = content[:max_chars] + "\n\n[已截断，完整内容见 path]"
        truncated = True

    summary = None
    if p.suffix.lower() == ".json" and not truncated:
        summary = _make_summary_for_json(p.read_text(encoding="utf-8", errors="replace"))
    if summary is None and truncated:
        summary = "内容已截断"

    return {"path": str(p), "content": content, "summary": summary}


@tool
@recorder.record
def write_tool(path: str, content: str) -> dict:
    """
    将文本写入文件。仅允许写入当前工作目录下的路径。

    Args:
        path: 写入路径，相对路径基于当前工作目录解析；父目录会自动创建。
        content: 要写入的字符串（可为 JSON 序列化后的字符串）。

    Returns:
        {"path": str, "summary": str | null}，如「已写入 N 字」。
    """
    try:
        p = _resolve_and_validate_path(path, allow_write=True)
    except ValueError as e:
        return {"path": path, "summary": None, "error": str(e)}

    try:
        p.write_text(content, encoding="utf-8")
    except Exception as e:
        return {"path": str(p), "summary": None, "error": str(e)}

    n = len(content)
    try:
        data = json.loads(content)
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            summary = f"已写入 JSON，共 {len(data['data'])} 条"
        else:
            summary = f"已写入 {n} 字"
    except Exception:
        summary = f"已写入 {n} 字"

    return {"path": str(p), "output_files": [str(p)], "summary": summary}
