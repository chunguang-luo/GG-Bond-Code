"""记忆系统 Prompt 段 — 注入到 System Prompt 中。

指导 AI 如何使用记忆系统：
- 何时写入记忆（四类分类法 + 排除规则）
- 如何保存（frontmatter 格式 + MEMORY.md 索引更新）
- 何时读取（系统自动注入）
"""

from __future__ import annotations

from .dir import DIR_EXISTS_GUIDANCE, ensure_memory_dir_exists
from .index import ENTRYPOINT_NAME, MAX_ENTRYPOINT_LINES, read_index
from .paths import get_auto_mem_path
from .types import MEMORY_TYPES, TYPE_META, WHAT_NOT_TO_SAVE


def load_memory_prompt(cwd: str | None = None) -> str | None:
    """构建记忆系统 Prompt 段。

    Returns:
        记忆系统指令文本，或 None（记忆禁用时）。
    """
    # 确保目录存在
    mem_dir = ensure_memory_dir_exists(cwd)

    # 读取现有索引
    index_content = read_index(cwd)

    sections = []

    # 1. 基础说明
    sections.append(f"""## Memory System

You have a persistent, file-based memory system at `{mem_dir}/`.
{DIR_EXISTS_GUIDANCE}

You should build up this memory system over time so that future conversations can have a complete picture of who the user is, how they'd like to collaborate with you, what behaviors to avoid or repeat, and the context behind the work the user gives you.

If the user explicitly asks you to remember something, save it immediately as whichever type fits best. If they ask you to forget something, find and remove the relevant entry.""")

    # 2. 四类分类法
    type_lines = []
    for mt in MEMORY_TYPES:
        meta = TYPE_META.get(mt, {})
        type_lines.append(f"- **{mt}**: {meta.get('meaning', '')} — write when {meta.get('write_when', '')}. Don't save: {meta.get('dont_save', '')}")
    sections.append("### Memory Types\n\n" + "\n".join(type_lines))

    # 3. 排除规则
    exclude_lines = [f"- {item}" for item in WHAT_NOT_TO_SAVE]
    sections.append("### What NOT to Save\n\n" + "\n".join(exclude_lines)
        + "\n\nThese exclusions apply even when the user explicitly asks you to save. "
        + "If they ask you to save a PR list or activity summary, ask what was *surprising* "
        + "or *non-obvious* about it — that is the part worth keeping.")

    # 4. 保存流程（两步）
    sections.append(f"""### How to Save Memories

Saving a memory is a two-step process:

**Step 1** — write the memory to its own file using this frontmatter format:
```markdown
---
name: {{{{memory name}}}}
description: {{{{one-line description — used to decide relevance in future conversations, so be specific}}}}
type: {{{{user, feedback, project, reference}}}}
---

{{{{memory content — for feedback/project types, structure as: rule/fact, then **Why:** and **How to apply:** lines}}}}
```

**Step 2** — add a pointer to that file in `{ENTRYPOINT_NAME}`. `{ENTRYPOINT_NAME}` is an index, not a memory — each entry should be one line, under ~150 characters: `- [Title](file.md) — one-line hook`. It has no frontmatter. Never write memory content directly into `{ENTRYPOINT_NAME}`.

- `{ENTRYPOINT_NAME}` is always loaded into your conversation context — lines after {MAX_ENTRYPOINT_LINES} will be truncated, so keep the index concise
- Keep the name, description, and type fields in memory files up-to-date with the content
- Organize memory semantically by topic, not chronologically
- Update or remove memories that turn out to be wrong or outdated
- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one.""")

    # 5. 现有索引内容
    if index_content and index_content.strip():
        sections.append(f"### Current Memory Index\n\n{index_content.strip()}")

    return "\n\n".join(sections)
