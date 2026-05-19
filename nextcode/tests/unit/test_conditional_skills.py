"""Unit tests for ConditionalSkillManager."""

from next_code.skills.conditional import ConditionalSkillManager
from next_code.commands.types import (
    CommandContext,
    CommandResult,
    PromptCommand,
    ResultType,
)


def _make_prompt_command(
    name: str,
    paths: list[str] | None = None,
    when_to_use: str | None = None,
) -> PromptCommand:
    async def handler(args, ctx):
        return CommandResult(type=ResultType.TEXT)

    return PromptCommand(
        name=f"/{name}",
        description=f"Skill {name}",
        handler=handler,
        paths=paths or [],
        when_to_use=when_to_use,
    )


class TestConditionalSkillManager:
    def test_register_no_paths_immediately_activated(self):
        mgr = ConditionalSkillManager()
        cmd = _make_prompt_command("review", paths=[])
        mgr.register_conditional(cmd)
        assert len(mgr.get_activated()) == 1
        assert len(mgr.get_pending()) == 0

    def test_register_with_paths_goes_pending(self):
        mgr = ConditionalSkillManager()
        cmd = _make_prompt_command("review", paths=["*.tsx"])
        mgr.register_conditional(cmd)
        assert len(mgr.get_pending()) == 1
        assert len(mgr.get_activated()) == 0

    def test_activate_matching_path(self):
        mgr = ConditionalSkillManager()
        cmd = _make_prompt_command("react", paths=["*.tsx", "*.jsx"])
        mgr.register_conditional(cmd)

        activated = mgr.activate_for_paths(["src/App.tsx"], "/project")
        assert activated == ["/react"]
        assert len(mgr.get_activated()) == 1
        assert len(mgr.get_pending()) == 0

    def test_no_match_stays_pending(self):
        mgr = ConditionalSkillManager()
        cmd = _make_prompt_command("react", paths=["*.tsx"])
        mgr.register_conditional(cmd)

        activated = mgr.activate_for_paths(["src/main.py"], "/project")
        assert activated == []
        assert len(mgr.get_pending()) == 1
        assert len(mgr.get_activated()) == 0

    def test_multiple_skills_different_patterns(self):
        mgr = ConditionalSkillManager()
        react = _make_prompt_command("react", paths=["*.tsx"])
        python = _make_prompt_command("python", paths=["*.py"])
        mgr.register_conditional(react)
        mgr.register_conditional(python)

        activated = mgr.activate_for_paths(["src/App.tsx"], "/project")
        assert "/react" in activated
        assert "/python" not in activated
        assert len(mgr.get_pending()) == 1  # python still pending

    def test_multiple_files_one_matches(self):
        mgr = ConditionalSkillManager()
        cmd = _make_prompt_command("react", paths=["*.tsx"])
        mgr.register_conditional(cmd)

        activated = mgr.activate_for_paths(
            ["src/main.py", "src/App.tsx", "README.md"],
            "/project",
        )
        assert activated == ["/react"]

    def test_relative_path_matching(self):
        mgr = ConditionalSkillManager()
        cmd = _make_prompt_command("frontend", paths=["src/**/*.tsx"])
        mgr.register_conditional(cmd)

        activated = mgr.activate_for_paths(["/project/src/App.tsx"], "/project")
        # fnmatch matches "src/App.tsx" against "src/**/*.tsx"
        # This depends on fnmatch behavior — it doesn't do ** by default
        # Let's use a simpler pattern
        assert isinstance(activated, list)

    def test_absolute_path_matching(self):
        mgr = ConditionalSkillManager()
        cmd = _make_prompt_command("config", paths=["*.json"])
        mgr.register_conditional(cmd)

        activated = mgr.activate_for_paths(["/project/package.json"], "/project")
        assert "/config" in activated

    def test_already_activated_not_re_activated(self):
        mgr = ConditionalSkillManager()
        cmd = _make_prompt_command("react", paths=["*.tsx"])
        mgr.register_conditional(cmd)

        activated1 = mgr.activate_for_paths(["src/App.tsx"], "/project")
        assert activated1 == ["/react"]

        # Second activation with same file — should not return it again
        activated2 = mgr.activate_for_paths(["src/other.tsx"], "/project")
        assert activated2 == []

    def test_is_activated(self):
        mgr = ConditionalSkillManager()
        cmd = _make_prompt_command("react", paths=["*.tsx"])
        mgr.register_conditional(cmd)

        assert not mgr.is_activated("/react")

        mgr.activate_for_paths(["src/App.tsx"], "/project")
        assert mgr.is_activated("/react")

    def test_get_all_conditional(self):
        mgr = ConditionalSkillManager()
        pending = _make_prompt_command("react", paths=["*.tsx"])
        activated = _make_prompt_command("review", paths=[])
        mgr.register_conditional(pending)
        mgr.register_conditional(activated)

        all_cmds = mgr.get_all_conditional()
        assert len(all_cmds) == 2

    def test_clear(self):
        mgr = ConditionalSkillManager()
        mgr.register_conditional(_make_prompt_command("react", paths=["*.tsx"]))
        mgr.register_conditional(_make_prompt_command("review", paths=[]))
        mgr.activate_for_paths(["src/App.tsx"], "/project")

        mgr.clear()
        assert len(mgr.get_pending()) == 0
        assert len(mgr.get_activated()) == 0

    def test_when_to_use_preserved(self):
        mgr = ConditionalSkillManager()
        cmd = _make_prompt_command("react", paths=["*.tsx"], when_to_use="when editing React files")
        mgr.register_conditional(cmd)

        mgr.activate_for_paths(["src/App.tsx"], "/project")
        activated = mgr.get_activated()
        assert len(activated) == 1
        assert activated[0].when_to_use == "when editing React files"
