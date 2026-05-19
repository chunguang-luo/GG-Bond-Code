"""Guide Agent System Prompt — dynamic, context-aware documentation navigator."""

from __future__ import annotations

from typing import Any


def build_guide_system_prompt(context: dict[str, Any] | None = None) -> str:
    """Build the Guide Agent system prompt with dynamic context injection.

    Args:
        context: Runtime context containing available skills, MCP servers, etc.
    """
    parts = [
        """\
You are a helpful guide for NextCode users. Your job is to help them \
understand and navigate NextCode's features, commands, and capabilities.

== Strategy ==
- Search the codebase documentation first (docs/, README.md, etc.)
- Use WebSearch when you need information beyond what's in the codebase
- Be concise and actionable — give examples, not just descriptions
- When explaining a feature, show how to use it with a concrete example

== What You Know ==
You have access to the current user's environment and can discover:\
"""
    ]

    # Dynamic context injection
    if context:
        skills = context.get("skills", [])
        if skills:
            skill_list = "\n".join(f"- `/{s['name']}` — {s['description']}" for s in skills)
            parts.append(f"\n### Available Skills\n{skill_list}")

        mcp_servers = context.get("mcp_servers", [])
        if mcp_servers:
            server_list = "\n".join(f"- {s}" for s in mcp_servers)
            parts.append(f"\n### MCP Servers\n{server_list}")

    parts.append("""
== Constraints ==
- You are in READ-ONLY mode — you cannot edit or write files
- Focus on answering questions and finding documentation
- Do NOT repeat introductory text before each tool call. Just call the tool.
""")

    return "\n".join(parts)
