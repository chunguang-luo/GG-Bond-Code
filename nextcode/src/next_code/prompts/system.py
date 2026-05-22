"""System Prompt assembly — mirrors prompts.ts section system."""

from __future__ import annotations

from next_code.prompts.sections.identity import section as identity_section
from next_code.prompts.sections.system import section as system_section
from next_code.prompts.sections.actions import section as actions_section
from next_code.prompts.sections.tool_guidelines import section as tool_guidelines_section
from next_code.prompts.sections.agent_guidelines import section as agent_guidelines_section
from next_code.prompts.sections.coding_preferences import section as coding_preferences_section
from next_code.prompts.sections.output_efficiency import section as output_efficiency_section
from next_code.prompts.sections.project_context import section as project_context_section

# Boundary marker separating static and dynamic sections
SYSTEM_PROMPT_DYNAMIC_BOUNDARY = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"


def build_system_prompt(cwd: str | None = None) -> list[str]:
    """Assemble the system prompt from sections.

    Returns:
        A list of prompt sections. Sections before SYSTEM_PROMPT_DYNAMIC_BOUNDARY
        are static (no parameters), sections after are dynamic (require parameters).

    Args:
        cwd: Current working directory for dynamic sections
    """
    sections = [
        # Static sections (no parameters)
        identity_section(),
        system_section(),
        actions_section(),
        tool_guidelines_section(),
        agent_guidelines_section(),
        coding_preferences_section(),
        output_efficiency_section(),

        # Boundary marker
        SYSTEM_PROMPT_DYNAMIC_BOUNDARY,

        # Dynamic sections (require parameters)
        *_get_dynamic_sections(cwd),
    ]

    # Filter out None/empty sections
    return [s for s in sections if s]


def _get_dynamic_sections(cwd: str | None = None) -> list[str]:
    """Get dynamic sections that should be after the boundary marker.

    Args:
        cwd: Current working directory

    Returns:
        List of dynamic section contents
    """
    sections = []

    if cwd:
        result = project_context_section(cwd)
        if result:
            sections.append(result)

        # Memory system section
        from next_code.memory.prompt import load_memory_prompt

        memory_prompt = load_memory_prompt(cwd)
        if memory_prompt:
            sections.append(memory_prompt)

    return sections
