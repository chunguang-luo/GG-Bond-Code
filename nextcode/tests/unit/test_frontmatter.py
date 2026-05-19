"""Unit tests for Skill frontmatter parser."""

from next_code.skills.frontmatter import SkillFrontmatter, parse_frontmatter


class TestSkillFrontmatterDefaults:
    def test_defaults(self):
        fm = SkillFrontmatter()
        assert fm.description == ""
        assert fm.allowed_tools == []
        assert fm.model is None
        assert fm.context == "inline"
        assert fm.agent is None
        assert fm.effort is None
        assert fm.when_to_use is None
        assert fm.argument_hint is None
        assert fm.user_invocable is True
        assert fm.paths == []
        assert fm.hooks == {}


class TestParseFrontmatter:
    def test_no_frontmatter(self):
        fm, body = parse_frontmatter("Hello world\nNo frontmatter here.")
        assert fm.description == ""
        assert body == "Hello world\nNo frontmatter here."

    def test_empty_frontmatter(self):
        content = "---\n---\nBody text"
        fm, body = parse_frontmatter(content)
        assert fm.description == ""
        assert body == "Body text"

    def test_description_field(self):
        content = "---\ndescription: Review code for bugs\n---\nReview the code."
        fm, body = parse_frontmatter(content)
        assert fm.description == "Review code for bugs"
        assert body == "Review the code."

    def test_model_field(self):
        content = "---\nmodel: claude-sonnet\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm.model == "claude-sonnet"

    def test_context_field(self):
        content = "---\ncontext: fork\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm.context == "fork"

    def test_allowed_tools_inline_list(self):
        content = "---\nallowed_tools: [Bash, Read, Write]\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm.allowed_tools == ["Bash", "Read", "Write"]

    def test_allowed_tools_multiline(self):
        content = "---\nallowed_tools:\n  - Bash\n  - Read\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm.allowed_tools == ["Bash", "Read"]

    def test_paths_field(self):
        content = "---\npaths: [*.tsx, *.jsx]\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm.paths == ["*.tsx", "*.jsx"]

    def test_when_to_use(self):
        content = "---\nwhen_to_use: when editing React files\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm.when_to_use == "when editing React files"

    def test_argument_hint(self):
        content = "---\nargument_hint: <files>\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm.argument_hint == "<files>"

    def test_user_invocable_false(self):
        content = "---\nuser_invocable: false\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm.user_invocable is False

    def test_user_invocable_true(self):
        content = "---\nuser_invocable: true\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm.user_invocable is True

    def test_effort_field(self):
        content = "---\neffort: high\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm.effort == "high"

    def test_agent_field(self):
        content = "---\nagent: code-reviewer\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm.agent == "code-reviewer"

    def test_quoted_values(self):
        content = '---\ndescription: "A quoted description"\n---\nBody'
        fm, body = parse_frontmatter(content)
        assert fm.description == "A quoted description"

    def test_comment_lines_ignored(self):
        content = "---\n# This is a comment\ndescription: Hello\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm.description == "Hello"

    def test_multiple_fields(self):
        content = "---\ndescription: Review code\nmodel: opus\ncontext: fork\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm.description == "Review code"
        assert fm.model == "opus"
        assert fm.context == "fork"

    def test_hyphenated_key_normalized(self):
        content = "---\nwhen-to-use: test\n---\nBody"
        fm, body = parse_frontmatter(content)
        assert fm.when_to_use == "test"
