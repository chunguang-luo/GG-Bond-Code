"""Unit tests for create_skill_command — Markdown → PromptCommand conversion."""

import asyncio

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


class TestCreateSkillCommand:
    def test_basic_creation(self):
        fm = SkillFrontmatter(description="Review code")
        cmd = create_skill_command(
            skill_name="review",
            markdown_content="Review the code for bugs.",
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
        fm = SkillFrontmatter()
        cmd = create_skill_command(
            skill_name="test",
            markdown_content="Body",
            frontmatter=fm,
            source="skills",
            loaded_from="skills",
        )
        assert cmd.description == "Skill: test"

    def test_get_prompt_basic(self):
        fm = SkillFrontmatter()
        cmd = create_skill_command(
            skill_name="review",
            markdown_content="Review the code for bugs.",
            frontmatter=fm,
            source="skills",
            loaded_from="skills",
        )
        ctx = _make_context()
        blocks = asyncio.run(cmd.get_prompt("", ctx))
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"
        assert "Review the code for bugs." in blocks[0]["text"]

    def test_argument_substitution(self):
        fm = SkillFrontmatter(argument_hint="<files>")
        cmd = create_skill_command(
            skill_name="review",
            markdown_content="Review ${files} for bugs.",
            frontmatter=fm,
            source="skills",
            loaded_from="skills",
        )
        ctx = _make_context()
        blocks = asyncio.run(cmd.get_prompt("src/main.py", ctx))
        assert "src/main.py" in blocks[0]["text"]
        assert "${files}" not in blocks[0]["text"]

    def test_multiple_argument_substitution(self):
        fm = SkillFrontmatter(argument_hint="<source> <target>")
        cmd = create_skill_command(
            skill_name="migrate",
            markdown_content="Migrate from ${source} to ${target}.",
            frontmatter=fm,
            source="skills",
            loaded_from="skills",
        )
        ctx = _make_context()
        blocks = asyncio.run(cmd.get_prompt("python typescript", ctx))
        assert "python" in blocks[0]["text"]
        assert "typescript" in blocks[0]["text"]
        assert "${source}" not in blocks[0]["text"]
        assert "${target}" not in blocks[0]["text"]

    def test_directory_variable_substitution(self):
        fm = SkillFrontmatter()
        cmd = create_skill_command(
            skill_name="review",
            markdown_content="Files are in ${NEXTCODE_SKILL_DIR}",
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
            markdown_content="Body",
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
        fm = SkillFrontmatter(argument_hint="<files>")
        cmd = create_skill_command(
            skill_name="review",
            markdown_content="Review ${files}",
            frontmatter=fm,
            source="skills",
            loaded_from="skills",
        )
        ctx = _make_context()
        # Empty args — no substitution
        blocks = asyncio.run(cmd.get_prompt("", ctx))
        assert "${files}" in blocks[0]["text"]
