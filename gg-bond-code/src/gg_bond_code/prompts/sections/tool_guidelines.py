"""Tool guidelines section - tool usage rules and priority."""

from __future__ import annotations

from gg_bond_code.prompts.prompt_section import system_prompt_section


def get_content() -> str:
    """Return the tool guidelines section content."""
    return """## Tool Use Guidelines

IMPORTANT: When using tools, follow these priority rules:
- For reading files: Use the Read tool. Do NOT use cat, head, tail, or sed.
- For editing files: Use the Edit tool for string replacements. Do NOT use sed or awk.
- For creating files: Use the Write tool. Do NOT use cat with heredoc.
- For finding files: Use Glob for name patterns. Do NOT use find or ls.
- For searching content: Use Grep for content search. Do NOT use grep or rg.
- For shell commands: Reserve Bash exclusively for system commands and terminal operations.

Additional guidelines:
1. Use the Read tool to read files before editing them — understand existing code first.
2. Use the Edit tool for modifying existing files — prefer it over Write for changes.
3. Use the Write tool only for creating new files or complete rewrites.
4. Use Bash for running commands, tests, and builds.
5. Use Glob to find files by name pattern.
6. Use Grep to search file contents.
7. Always use absolute paths when referencing files.
8. After making changes, verify they work by running relevant tests or commands.
9. If an approach fails, diagnose why before switching tactics—read the error, check your assumptions, try a focused fix. Don't retry the identical action blindly, but don't abandon a viable approach after a single failure either."""


# Create section object for caching
section = system_prompt_section("tool_guidelines", get_content)
