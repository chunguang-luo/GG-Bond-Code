"""Skill loading system — Markdown → PromptCommand conversion pipeline."""

from .frontmatter import SkillFrontmatter, parse_frontmatter
from .loader import load_skills_from_dir, discover_skill_dirs_for_paths
from .create_command import create_skill_command
from .conditional import ConditionalSkillManager

__all__ = [
    "SkillFrontmatter",
    "parse_frontmatter",
    "load_skills_from_dir",
    "discover_skill_dirs_for_paths",
    "create_skill_command",
    "ConditionalSkillManager",
]
