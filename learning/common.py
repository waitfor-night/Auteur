from __future__ import annotations

from pathlib import Path


def resolve_trace_paths(paths: list) -> list:
    """
    将若干路径（文件或目录）解析为轨迹文件列表。
    - 若为文件：直接加入列表。
    - 若为目录：递归收集该目录及所有子目录下的 ctx*.json 文件（RunContext）。
    返回去重且排序后的 Path 列表。
    """
    resolved = []
    seen = set()
    for p in paths:
        path = Path(p).resolve()
        if path.is_file():
            if path not in seen:
                seen.add(path)
                resolved.append(path)
        elif path.is_dir():
            for f in sorted(path.rglob("ctx*.json")):
                f = f.resolve()
                if f not in seen:
                    seen.add(f)
                    resolved.append(f)
        else:
            raise FileNotFoundError(f"Not a file or directory: {path}")
    return sorted(resolved)
