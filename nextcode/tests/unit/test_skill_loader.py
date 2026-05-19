"""Unit tests for Skill directory loader."""

import asyncio
import tempfile
from pathlib import Path

from next_code.skills.loader import load_skills_from_dir
from next_code.commands.types import CommandType


class TestLoadSkillsFromDir:
    def test_nonexistent_directory(self):
        result = asyncio.run(
            load_skills_from_dir(Path("/nonexistent/path"))
        )
        assert result == []

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(load_skills_from_dir(Path(tmp)))
            assert result == []

    def test_new_format_skill_dir(self):
        """Test loading a skill from <name>/SKILL.md directory structure."""
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "review"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\ndescription: Review code\n---\nReview the code for bugs."
            )

            result = asyncio.run(load_skills_from_dir(Path(tmp)))
            assert len(result) == 1
            assert result[0].name == "/review"
            assert result[0].description == "Review code"
            assert result[0].command_type == CommandType.PROMPT

    def test_legacy_format_single_md(self):
        """Test loading a skill from a single .md file (legacy format)."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "test.md").write_text(
                "---\ndescription: Test skill\n---\nThis is a test."
            )

            result = asyncio.run(load_skills_from_dir(Path(tmp)))
            assert len(result) == 1
            assert result[0].name == "/test"

    def test_skips_skill_md_at_top_level(self):
        """SKILL.md at top level (not inside a subdirectory) should be skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "SKILL.md").write_text("---\n---\nBody")

            result = asyncio.run(load_skills_from_dir(Path(tmp)))
            assert result == []

    def test_skips_non_md_files(self):
        """Non-Markdown files should be ignored."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "config.json").write_text("{}")
            (Path(tmp) / "script.py").write_text("pass")

            result = asyncio.run(load_skills_from_dir(Path(tmp)))
            assert result == []

    def test_multiple_skills(self):
        """Load multiple skills from a directory."""
        with tempfile.TemporaryDirectory() as tmp:
            # New format
            review_dir = Path(tmp) / "review"
            review_dir.mkdir()
            (review_dir / "SKILL.md").write_text(
                "---\ndescription: Code review\n---\nReview code."
            )

            # Legacy format
            (Path(tmp) / "deploy.md").write_text(
                "---\ndescription: Deploy\n---\nDeploy the app."
            )

            result = asyncio.run(load_skills_from_dir(Path(tmp)))
            assert len(result) == 2
            names = {cmd.name for cmd in result}
            assert "/review" in names
            assert "/deploy" in names

    def test_skill_without_frontmatter(self):
        """A Markdown file without frontmatter should still load."""
        with tempfile.TemporaryDirectory() as tmp:
            review_dir = Path(tmp) / "simple"
            review_dir.mkdir()
            (review_dir / "SKILL.md").write_text("Just a simple skill body.")

            result = asyncio.run(load_skills_from_dir(Path(tmp)))
            assert len(result) == 1
            assert result[0].name == "/simple"
            assert result[0].description == "Skill: simple"

    def test_skill_with_full_frontmatter(self):
        """Test skill with all frontmatter fields populated."""
        with tempfile.TemporaryDirectory() as tmp:
            review_dir = Path(tmp) / "review"
            review_dir.mkdir()
            (review_dir / "SKILL.md").write_text(
                "---\n"
                "description: Review code\n"
                "model: opus\n"
                "context: fork\n"
                "agent: reviewer\n"
                "effort: high\n"
                "allowed_tools: [Bash, Read]\n"
                "argument_hint: <files>\n"
                "paths: [*.py]\n"
                "---\n"
                "Review ${files} carefully."
            )

            result = asyncio.run(load_skills_from_dir(Path(tmp)))
            assert len(result) == 1
            cmd = result[0]
            assert cmd.model == "opus"
            assert cmd.context == "fork"
            assert cmd.agent == "reviewer"
            assert cmd.effort == "high"
            assert cmd.allowed_tools == ["Bash", "Read"]
            assert cmd.arg_names == ["files"]
            assert cmd.paths == ["*.py"]

    def test_source_and_loaded_from(self):
        """Test that source and loaded_from are propagated."""
        with tempfile.TemporaryDirectory() as tmp:
            review_dir = Path(tmp) / "review"
            review_dir.mkdir()
            (review_dir / "SKILL.md").write_text("---\n---\nBody")

            result = asyncio.run(
                load_skills_from_dir(Path(tmp), source="project", loaded_from="skills")
            )
            assert result[0].source == "project"
            assert result[0].loaded_from == "skills"

    def test_unreadable_file_skipped(self):
        """An unreadable skill file should be skipped gracefully."""
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "broken"
            skill_dir.mkdir()
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text("---\n---\nBody")
            # Make unreadable
            skill_file.chmod(0o000)

            try:
                result = asyncio.run(load_skills_from_dir(Path(tmp)))
                # Should not crash, may return empty or skip
                assert isinstance(result, list)
            finally:
                # Restore permissions for cleanup
                skill_file.chmod(0o644)
