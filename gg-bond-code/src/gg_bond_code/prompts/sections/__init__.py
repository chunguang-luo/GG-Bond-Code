"""Sections module - individual system prompt sections.

Each section is in its own file for better organization.
Import sections from here:
    from gg_bond_code.prompts.sections import (
        identity_section,
        system_section,
        actions_section,
        ...
    )
"""

from __future__ import annotations

from gg_bond_code.prompts.sections.identity import section as identity_section
from gg_bond_code.prompts.sections.system import section as system_section
from gg_bond_code.prompts.sections.actions import section as actions_section
from gg_bond_code.prompts.sections.tool_guidelines import section as tool_guidelines_section
from gg_bond_code.prompts.sections.coding_preferences import section as coding_preferences_section
from gg_bond_code.prompts.sections.output_efficiency import section as output_efficiency_section
from gg_bond_code.prompts.sections.project_context import section as project_context_section


__all__ = [
    "identity_section",
    "system_section",
    "actions_section",
    "tool_guidelines_section",
    "coding_preferences_section",
    "output_efficiency_section",
    "project_context_section",
]
