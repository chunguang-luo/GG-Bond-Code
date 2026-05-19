"""Unit tests for create_skill_command — Markdown → PromptCommand conversion."""

import asyncio
import tempfile
from pathlib import Path

from next_code.skills.frontmatter import SkillFrontmatter
from next_code.skills.create_command import create_skill_command
from next_code.commands.types import CommandContext, CommandType, ResultType
from next_code.commands.registry import CommandRegistry


def _make_context(**overrides):
    store: dict[str, object] = {}

    defaults = {
        "model": "deepseek-chat",
        "store_get": lambda k, d=None: store.get(k, d),
        "store_set": lambda k, v: store.__setitem__(k, v),
        "loop_state": None,
        "clear_system_context_cache": lambda: None,
        "registry": CommandRegistry(),
    }
    defaults.update(overrides)
    return CommandContext(**defaults)


def _create_skill_file(content: str) -> str:
    """Write a skill file to a temp directory and return its path."""
    tmp = tempfile.mkdtemp()
    path = Path(tmp) / "SKILL.md"
    path.write_text(content, encoding="utf-8")
    return str(path)


class TestCreateSkillCommand:
    def test_basic_creation(self):
        path = _create_skill_file("---\ndescription: Review code\n---\nReview the code for bugs.")
        fm = SkillFrontmatter(description="Review code")
        cmd = create_skill_command(
            skill_name="review",
            file_path=path,
            frontmatter=fm,
            source="skills",
            loaded_from="skills",
        )
        assert cmd.name == "/review"
        assert cmd.description == "Review code"
        assert cmd.command_type == CommandType.PROMPT
        assert cmd.source == "skills"
        assert cmd.loaded_from == "skills"
        assert cmd.get_prompt is not None

    def test_default_description(self):
        path = _create_skill_file("---\n---\nBody")
        fm = SkillFrontmatter()
        cmd = create_skill_command(
            skill_name="test",
            file_path=path,
            frontmatter=fm,
            source="skills",
            loaded_from="skills",
        )
        assert cmd.description == "Skill: test"

    def test_get_prompt_lazy_reads_body(self):
        """get_prompt reads the Markdown body from the file on first call."""
        path = _create_skill_file("---\n---\nReview the code for bugs.")
        fm = SkillFrontmatter()
        cmd = create_skill_command(
            skill_name="review",
            file_path=path,
            frontmatter=fm,
            source="skills",
            loaded_from="skills",
        )
        ctx = _make_context()
        blocks = asyncio.run(cmd.get_prompt("", ctx))
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"
        assert "Review the code for bugs." in blocks[0]["text"]

    def test_get_prompt_caches_body(self):
        """Second get_prompt call uses cached body."""
        path = _create_skill_file("---\n---\nReview the code.")
        fm = SkillFrontmatter()
        cmd = create_skill_command(
            skill_name="review",
            file_path=path,
            frontmatter=fm,
            source="skills",
            loaded_from="skills",
        )
        ctx = _make_context()
        # First call reads the file
        blocks1 = asyncio.run(cmd.get_prompt("", ctx))
        # Second call uses cache (even if file is deleted)
        Path(path).unlink()
        blocks2 = asyncio.run(cmd.get_prompt("", ctx))
        assert blocks1[0]["text"] == blocks2[0]["text"]

    def test_argument_substitution(self):
        path = _create_skill_file("---\n---\nReview ${files} for bugs.")
        fm = SkillFrontmatter(argument_hint="<files>")
        cmd = create_skill_command(
            skill_name="review",
            file_path=path,
            frontmatter=fm,
            source="skills",
            loaded_from="skills",
        )
        ctx = _make_context()
        blocks = asyncio.run(cmd.get_prompt("src/main.py", ctx))
        assert "src/main.py" in blocks[0]["text"]
        assert "${files}" not in blocks[0]["text"]

    def test_multiple_argument_substitution(self):
        path = _create_skill_file("---\n---\nMigrate from ${source} to ${target}.")
        fm = SkillFrontmatter(argument_hint="<source> <target>")
        cmd = create_skill_command(
            skill_name="migrate",
            file_path=path,
            frontmatter=fm,
            source="skills",
            loaded_from="skills",
        )
        ctx = _make_context()
        blocks = asyncio.run(cmd.get_prompt("python typescript", ctx))
        assert "python" in blocks[0]["text"]
        assert "typescript" in blocks[0]["text"]

    def test_directory_variable_substitution(self):
        path = _create_skill_file("---\n---\nFiles are in ${NEXTCODE_SKILL_DIR}")
        fm = SkillFrontmatter()
        cmd = create_skill_command(
            skill_name="review",
            file_path=path,
            frontmatter=fm,
            source="skills",
            loaded_from="skills",
            base_dir="/home/user/.nextcode/skills/review",
        )
        ctx = _make_context()
        blocks = asyncio.run(cmd.get_prompt("", ctx))
        assert "/home/user/.nextcode/skills/review" in blocks[0]["text"]
        assert "${NEXTCODE_SKILL_DIR}" not in blocks[0]["text"]

    def test_frontmatter_fields_mapped(self):
        path = _create_skill_file("---\n---\nBody")
        fm = SkillFrontmatter(
            description="Review code",
            allowed_tools=["Bash", "Read"],
            model="opus",
            context="fork",
            agent="reviewer",
            effort="high",
            when_to_use="when reviewing code",
            user_invocable=True,
            paths=["*.py"],
        )
        cmd = create_skill_command(
            skill_name="review",
            file_path=path,
            frontmatter=fm,
            source="project",
            loaded_from="skills",
        )
        assert cmd.allowed_tools == ["Bash", "Read"]
        assert cmd.model == "opus"
        assert cmd.context == "fork"
        assert cmd.agent == "reviewer"
        assert cmd.effort == "high"
        assert cmd.when_to_use == "when reviewing code"
        assert cmd.user_invocable is True
        assert cmd.paths == ["*.py"]
        assert cmd.source == "project"

    def test_no_args_no_substitution(self):
        path = _create_skill_file("---\n---\nReview ${files}")
        fm = SkillFrontmatter(argument_hint="<files>")
        cmd = create_skill_command(
            skill_name="review",
            file_path=path,
            frontmatter=fm,
            source="skills",
            loaded_from="skills",
        )
        ctx = _make_context()
        # Empty args — no substitution
        blocks = asyncio.run(cmd.get_prompt("", ctx))
        assert "${files}" in blocks[0]["text"]

    def test_missing_file_returns_empty(self):
        """If the skill file is deleted before get_prompt, returns empty text."""
        path = _create_skill_file("---\n---\nBody")
        fm = SkillFrontmatter()
        cmd = create_skill_command(
            skill_name="review",
            file_path=path,
            frontmatter=fm,
            source="skills",
            loaded_from="skills",
        )
        # Delete the file before invoking get_prompt
        Path(path).unlink()
        ctx = _make_context()
        blocks = asyncio.run(cmd.get_prompt("", ctx))
        assert len(blocks) == 1
        assert blocks[0]["text"] == ""
