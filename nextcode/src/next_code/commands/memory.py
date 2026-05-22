"""Built-in /memory command."""

from __future__ import annotations

import os
from pathlib import Path

from .types import CommandContext, CommandResult, LocalCommand, ResultType


async def handle_memory(args: str, context: CommandContext) -> CommandResult:
    """Handle /memory: show memory index and list."""
    cwd = context.store_get("cwd", os.getcwd())

    # Import memory modules
    from ..memory.paths import get_auto_mem_path
    from ..memory.index import ENTRYPOINT_NAME
    from ..memory.scan import scan_memory_files

    memory_dir = get_auto_mem_path(cwd)
    index_path = Path(memory_dir) / ENTRYPOINT_NAME

    args_str = args.strip()
    parts = args_str.split(maxsplit=1)
    subcmd = parts[0].lower() if parts else ""
    type_filter = parts[1].lower() if len(parts) > 1 else ""

    # /memory list [type]
    if subcmd == "list":
        return await _list_memories(memory_dir, type_filter)

    # /memory <type> — filter by type
    if subcmd in ("user", "feedback", "project", "reference"):
        return await _list_memories(memory_dir, subcmd)

    # /memory <name> — query specific memory file
    if args_str:
        return await _query_memory(memory_dir, args_str)

    # /memory (default): show index
    return await _show_index(index_path, memory_dir)


async def _show_index(index_path: Path, memory_dir: str) -> CommandResult:
    """Show MEMORY.md index content."""
    if not index_path.exists():
        return CommandResult(
            type=ResultType.TEXT,
            content={"message": f"No memory index found at {memory_dir}/"},
        )

    content = index_path.read_text(encoding="utf-8").strip()
    if not content:
        return CommandResult(
            type=ResultType.TEXT,
            content={"message": "Memory index is empty."},
        )

    return CommandResult(
        type=ResultType.TEXT,
        content={
            "title": "Memory Index",
            "content": content,
            "memory_dir": memory_dir,
        },
    )


async def _query_memory(memory_dir: str, name: str) -> CommandResult:
    """Query a specific memory file by name (with or without .md extension)."""
    if not name.endswith(".md"):
        name += ".md"

    filepath = Path(memory_dir) / name

    # Try exact match first
    if not filepath.exists():
        # Try case-insensitive match
        try:
            for entry in Path(memory_dir).iterdir():
                if entry.name.lower() == name.lower() and entry.suffix == ".md":
                    filepath = entry
                    break
            else:
                return CommandResult(
                    type=ResultType.TEXT,
                    content={"message": f"Memory not found: {name}"},
                )
        except OSError:
            return CommandResult(
                type=ResultType.TEXT,
                content={"message": f"Memory directory not found: {memory_dir}"},
            )

    try:
        content = filepath.read_text(encoding="utf-8")
    except OSError:
        return CommandResult(
            type=ResultType.TEXT,
            content={"message": f"Failed to read memory file: {filepath}"},
        )

    return CommandResult(
        type=ResultType.TEXT,
        content={
            "title": name.replace(".md", ""),
            "content": content,
            "filepath": str(filepath),
        },
    )


async def _list_memories(memory_dir: str, type_filter: str) -> CommandResult:
    """List all memories, optionally filtered by type."""
    from ..memory.scan import scan_memory_files
    from ..memory.types import MemoryType

    if not Path(memory_dir).exists():
        return CommandResult(
            type=ResultType.TEXT,
            content={"message": f"Memory directory not found: {memory_dir}"},
        )

    headers = scan_memory_files(memory_dir)

    # Filter by type if specified
    valid_types = {t.value for t in MemoryType}
    if type_filter and type_filter in valid_types:
        headers = [h for h in headers if h.mem_type.value == type_filter]

    if not headers:
        filter_msg = f" (type: {type_filter})" if type_filter else ""
        return CommandResult(
            type=ResultType.TEXT,
            content={"message": f"No memories found{filter_msg}."},
        )

    # Build output
    lines = [f"Memory files in {memory_dir}:", ""]

    # Group by type
    by_type: dict[str, list] = {}
    for header in headers:
        t = header.mem_type.value
        if t not in by_type:
            by_type[t] = []
        by_type[t].append(header)

    for mem_type in ("user", "feedback", "project", "reference"):
        items = by_type.get(mem_type, [])
        if not items:
            continue
        lines.append(f"## {mem_type}")
        for h in items:
            lines.append(f"- **{h.filename.replace('.md', '')}**: {h.description}")
        lines.append("")

    return CommandResult(
        type=ResultType.TEXT,
        content={
            "title": f"Memory List{type_filter and f' ({type_filter})' or ''}",
            "content": "\n".join(lines).strip(),
            "count": len(headers),
        },
    )


def create() -> LocalCommand:
    return LocalCommand(
        name="/memory",
        description="Show memory index and list memories",
        handler=handle_memory,
    )