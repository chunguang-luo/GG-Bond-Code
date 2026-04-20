"""System Prompt assembly — mirrors prompts.ts section system."""

from __future__ import annotations

from gg_bond_code.prompts.sections.identity import section as identity_section
from gg_bond_code.prompts.sections.system import section as system_section
from gg_bond_code.prompts.sections.actions import section as actions_section
from gg_bond_code.prompts.sections.tool_guidelines import section as tool_guidelines_section
from gg_bond_code.prompts.sections.coding_preferences import section as coding_preferences_section
from gg_bond_code.prompts.sections.output_efficiency import section as output_efficiency_section
from gg_bond_code.prompts.sections.project_context import section as project_context_section
from gg_bond_code.prompts.prompt_section import (
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
)


def build_system_prompt(cwd: str | None = None) -> list[str]:
    """Assemble the system prompt from sections.

    Returns:
        A list of prompt sections. Sections before SYSTEM_PROMPT_DYNAMIC_BOUNDARY
        are static (no parameters), sections after are dynamic (require parameters).

    Args:
        cwd: Current working directory for dynamic sections
    """
    sections = [
        # Static sections (cacheable across sessions)
        identity_section.resolve(),
        system_section.resolve(),
        actions_section.resolve(),
        tool_guidelines_section.resolve(),
        coding_preferences_section.resolve(),
        output_efficiency_section.resolve(),

        # Boundary marker
        SYSTEM_PROMPT_DYNAMIC_BOUNDARY,

        # Dynamic sections (per-session)
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
        result = project_context_section.resolve(cwd)
        if result:
            sections.append(result)

    return sections
