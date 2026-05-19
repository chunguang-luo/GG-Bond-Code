"""Unit tests for dynamic skill directory discovery."""

import tempfile
from pathlib import Path

from next_code.skills.loader import discover_skill_dirs_for_paths


class TestDiscoverSkillDirsForPaths:
    def test_no_discovery_in_flat_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = str(Path(tmp).resolve())
            # No .nextcode/skills/ anywhere
            result = discover_skill_dirs_for_paths(
                [f"{cwd}/src/main.py"],
                cwd,
            )
            assert result == []

    def test_discovers_skills_dir_near_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = str(Path(tmp).resolve())
            # Create .nextcode/skills/ next to the file
            project_dir = Path(cwd) / "project"
            project_dir.mkdir()
            skills_dir = project_dir / ".nextcode" / "skills"
            skills_dir.mkdir(parents=True)

            result = discover_skill_dirs_for_paths(
                [f"{project_dir}/main.py"],
                cwd,
            )
            assert len(result) == 1
            assert result[0] == skills_dir

    def test_discovers_at_cwd_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = str(Path(tmp).resolve())
            skills_dir = Path(cwd) / ".nextcode" / "skills"
            skills_dir.mkdir(parents=True)

            result = discover_skill_dirs_for_paths(
                [f"{cwd}/main.py"],
                cwd,
            )
            assert len(result) == 1
            assert result[0] == skills_dir

    def test_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = str(Path(tmp).resolve())
            skills_dir = Path(cwd) / ".nextcode" / "skills"
            skills_dir.mkdir(parents=True)

            # Two files in same directory should not produce duplicate entries
            result = discover_skill_dirs_for_paths(
                [f"{cwd}/main.py", f"{cwd}/other.py"],
                cwd,
            )
            assert len(result) == 1

    def test_discovers_multiple_levels(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = str(Path(tmp).resolve())
            # Root-level skills
            root_skills = Path(cwd) / ".nextcode" / "skills"
            root_skills.mkdir(parents=True)

            # Sub-project skills
            subproject = Path(cwd) / "packages" / "frontend"
            subproject.mkdir(parents=True)
            sub_skills = subproject / ".nextcode" / "skills"
            sub_skills.mkdir(parents=True)

            result = discover_skill_dirs_for_paths(
                [f"{subproject}/App.tsx"],
                cwd,
            )
            assert len(result) == 2
            result_paths = {str(p) for p in result}
            assert str(sub_skills) in result_paths
            assert str(root_skills) in result_paths

    def test_stops_at_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = str(Path(tmp).resolve())
            # File outside cwd should not trigger upward walk beyond cwd
            result = discover_skill_dirs_for_paths(
                ["/tmp/some/other/place/main.py"],
                cwd,
            )
            # File doesn't start with cwd, so no upward walk happens
            assert result == []

    def test_empty_file_paths(self):
        result = discover_skill_dirs_for_paths([], "/project")
        assert result == []

    def test_nonexistent_file_still_works(self):
        """Discovery is path-based — the file doesn't need to exist."""
        with tempfile.TemporaryDirectory() as tmp:
            cwd = str(Path(tmp).resolve())
            skills_dir = Path(cwd) / ".nextcode" / "skills"
            skills_dir.mkdir(parents=True)

            result = discover_skill_dirs_for_paths(
                [f"{cwd}/nonexistent.py"],
                cwd,
            )
            assert len(result) == 1
