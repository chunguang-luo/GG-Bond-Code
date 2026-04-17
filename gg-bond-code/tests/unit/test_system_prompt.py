"""Test System Prompt refactoring - Phase 1."""

import pytest

from gg_bond_code.prompts.system import (
    build_system_prompt,
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
)
from gg_bond_code.api.client import _split_system_prompt


@pytest.fixture
def temp_project_with_ggbond(tmp_path, monkeypatch):
    """Create a temporary project with GGBOND.md in .ggbond directory."""
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()

    # Create .ggbond directory and GGBOND.md
    ggbond_dir = project_dir / ".ggbond"
    ggbond_dir.mkdir()
    ggbond_md = ggbond_dir / "GGBOND.md"
    ggbond_md.write_text("# Project Notes\nThis is a test project memory file.")

    # Create .git directory to simulate git repo
    (project_dir / ".git").mkdir()

    return str(project_dir)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository."""
    import subprocess

    project_dir = tmp_path / "test_repo"
    project_dir.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=project_dir, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=project_dir, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=project_dir, capture_output=True, check=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=project_dir, capture_output=True, check=True)

    return str(project_dir)


def test_build_system_prompt_returns_list():
    """Test that build_system_prompt returns a list."""
    result = build_system_prompt("/tmp")
    assert isinstance(result, list), f"Expected list, got {type(result)}"
    assert len(result) > 0, "Result should not be empty"


def test_boundary_marker_present():
    """Test that boundary marker is in the prompt."""
    result = build_system_prompt("/tmp")
    assert SYSTEM_PROMPT_DYNAMIC_BOUNDARY in result, "Boundary marker not found"


def test_static_sections_before_boundary():
    """Test that static sections come before boundary."""
    result = build_system_prompt("/tmp")
    boundary_idx = result.index(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
    static_sections = result[:boundary_idx]

    # Check that static sections exist
    assert len(static_sections) > 0, "No static sections found"

    # Check that no static section contains "## Project Context" (dynamic)
    assert all("## Project Context" not in s for s in static_sections), \
        "Static sections should not contain dynamic content"

    # Verify expected static sections are present
    section_names = [s.split("\n")[0] if s else "" for s in static_sections]
    assert any("GG Bond Code" in s for s in static_sections), "Identity section missing"


def test_dynamic_sections_after_boundary():
    """Test that dynamic sections come after boundary."""
    result = build_system_prompt("/tmp")
    boundary_idx = result.index(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
    dynamic_sections = result[boundary_idx + 1:]

    # Project context should be in dynamic sections when cwd is provided
    assert any("## Project Context" in s for s in dynamic_sections), \
        "Project context should be in dynamic sections"


def test_split_system_prompt_with_list():
    """Test _split_system_prompt with list input."""
    system_list = [
        "Static section 1",
        "Static section 2",
        SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
        "Dynamic section 1",
        "Dynamic section 2",
    ]
    static, dynamic = _split_system_prompt(system_list)

    assert len(static) == 2, f"Expected 2 static sections, got {len(static)}"
    assert len(dynamic) == 2, f"Expected 2 dynamic sections, got {len(dynamic)}"
    assert static == ["Static section 1", "Static section 2"]
    assert dynamic == ["Dynamic section 1", "Dynamic section 2"]


def test_split_system_prompt_with_string():
    """Test _split_system_prompt with string input."""
    system_str = "Single string system prompt"
    static, dynamic = _split_system_prompt(system_str)

    assert len(static) == 1, f"Expected 1 static section, got {len(static)}"
    assert len(dynamic) == 0, f"Expected 0 dynamic sections, got {len(dynamic)}"
    assert static == ["Single string system prompt"]


def test_split_system_prompt_no_boundary():
    """Test _split_system_prompt when no boundary marker."""
    system_list = [
        "Section 1",
        "Section 2",
        "Section 3",
    ]
    static, dynamic = _split_system_prompt(system_list)

    assert len(static) == 3, f"Expected 3 static sections, got {len(static)}"
    assert len(dynamic) == 0, f"Expected 0 dynamic sections, got {len(dynamic)}"


def test_build_system_prompt_sections_order():
    """Test that sections are in the correct order."""
    result = build_system_prompt("/tmp")
    boundary_idx = result.index(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
    static_sections = result[:boundary_idx]

    # Verify expected sections exist in static part
    section_texts = " ".join(static_sections)
    assert "GG Bond Code" in section_texts, "Identity section missing"
    assert "## System" in section_texts, "System section missing"
    assert "## Actions" in section_texts, "Actions section missing"
    assert "## Tool Use Guidelines" in section_texts, "Tool guidelines missing"
    assert "## Coding Preferences" in section_texts, "Coding preferences missing"
    assert "## Output Efficiency" in section_texts, "Output efficiency missing"


def test_build_system_prompt_no_cwd():
    """Test build_system_prompt when cwd is None."""
    result = build_system_prompt(None)
    assert isinstance(result, list)
    boundary_idx = result.index(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
    dynamic_sections = result[boundary_idx + 1:]

    # No project context should be present
    assert not any("## Project Context" in s for s in dynamic_sections), \
        "Project context should not be present when cwd is None"


def test_build_system_prompt_with_ggbond_md(temp_project_with_ggbond):
    """Test build_system_prompt works with GGBOND.md.

    Note: GGBOND.md is now handled by the context layer (user context),
    not directly by build_system_prompt. This test verifies that the old
    functionality (direct GGBOND.md inclusion) is removed.
    """
    result = build_system_prompt(temp_project_with_ggbond)
    assert isinstance(result, list)

    result_text = "\n\n".join(result)
    # GGBOND.md should NOT be in system prompt (moved to user context)
    assert "## GGBOND Project Memory" not in result_text, \
        "GGBOND.md should not be in system prompt (moved to user context)"


def test_build_system_prompt_without_ggbond_md():
    """Test build_system_prompt works without GGBOND.md.

    Note: GGBOND.md is now handled by user context, not system prompt.
    This test verifies the old functionality is removed.
    """
    result = build_system_prompt("/tmp")
    assert isinstance(result, list)

    result_text = "\n\n".join(result)
    # Should not include GGBOND section if file doesn't exist
    assert "## GGBOND Project Memory" not in result_text, \
        "GGBOND.md section should not be in system prompt (moved to user context)"


def test_build_system_prompt_git_status(temp_git_repo):
    """Test build_system_prompt works in git repository.

    Note: Git status is now handled by the context layer (system context),
    not directly by build_system_prompt. This test verifies that the old
    functionality (direct Git status inclusion) is removed.
    """
    result = build_system_prompt(temp_git_repo)
    assert isinstance(result, list)

    result_text = "\n\n".join(result)
    # Git status should NOT be in system prompt (moved to system context)
    assert "## Git Status" not in result_text, \
        "Git status should not be in system prompt (moved to system context)"


def test_build_system_prompt_no_git_repo():
    """Test build_system_prompt works outside git repository.

    Note: Git status is now handled by system context layer, not in
    build_system_prompt. This test verifies that the old functionality
    (direct Git status inclusion in dynamic sections) is removed.
    """
    result = build_system_prompt("/tmp")
    assert isinstance(result, list)

    result_text = "\n\n".join(result)
    # Should not include Git status in dynamic sections
    assert "## Git Status" not in result_text, \
        "Git status should not be included in dynamic sections (moved to system context)"


def test_dynamic_sections_order():
    """Test that dynamic sections appear in correct order.

    Note: Git status and GGBOND.md are now handled by context layers,
    not in dynamic sections. This test verifies that only project context
    remains in dynamic sections.
    """
    result = build_system_prompt("/tmp")
    boundary_idx = result.index(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
    dynamic_sections = result[boundary_idx + 1:]

    # Only Project Context should be present
    assert len(dynamic_sections) == 1, "Should only have Project Context in dynamic sections"
    assert "## Project Context" in dynamic_sections[0]

    # Git status and GGBOND.md should NOT be in dynamic sections
    assert not any("## Git Status" in s for s in dynamic_sections), \
        "Git status should not be in dynamic sections (moved to system context)"
    assert not any("## GGBOND Project Memory" in s for s in dynamic_sections), \
        "GGBOND.md should not be in dynamic sections (moved to user context)"
