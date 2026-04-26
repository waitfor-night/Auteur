from __future__ import annotations

from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parent.parent / "docs/skills/video-generation/reference/workspace_templates"


def ensure_user_memory(project_root: Path, username: str) -> Path:
    """Create workspace/<username>/memory/memory.md from template if absent."""
    memory_dir = project_root / "workspace" / username.strip() / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_md = memory_dir / "memory.md"
    if not memory_md.exists():
        template = _TEMPLATES / "memory.md"
        memory_md.write_text(
            template.read_text(encoding="utf-8") if template.is_file() else "# User Memory\n",
            encoding="utf-8",
        )
    return memory_md


def ensure_content_strategy(project_root: Path, username: str) -> Path:
    """Create workspace/<username>/content_strategy.md from template if absent."""
    user_dir = project_root / "workspace" / username.strip()
    user_dir.mkdir(parents=True, exist_ok=True)
    cs_md = user_dir / "content_strategy.md"
    if not cs_md.exists():
        template = _TEMPLATES / "content_strategy.md"
        cs_md.write_text(
            template.read_text(encoding="utf-8") if template.is_file() else "# Content Strategy\n",
            encoding="utf-8",
        )
    return cs_md
