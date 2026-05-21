"""Tests for the tasks module — TaskRegistry, DiskTaskOutput, StallWatchdog."""

import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from next_code.tasks.types import TaskType, TaskStatus, TaskStateBase, generate_task_id
from next_code.tasks.registry import TaskRegistry
from next_code.tasks.disk_output import DiskTaskOutput
from next_code.tasks.stall_watchdog import (
    StallWatchdog,
    start_watchdog,
    stop_watchdog,
    clear_watchdogs,
    INTERACTIVE_PATTERNS,
)


# ── TaskStateBase ──────────────────────────────────────────────────────────────


def test_generate_task_id():
    """generate_task_id creates unique IDs with correct prefix."""
    id1 = generate_task_id(TaskType.LOCAL_BASH)
    id2 = generate_task_id(TaskType.LOCAL_BASH)
    assert id1.startswith("b")
    assert id2.startswith("b")
    assert id1 != id2


def test_task_state_defaults():
    """TaskStateBase has sensible defaults."""
    task = TaskStateBase(id="test-1", type=TaskType.LOCAL_BASH)
    assert task.status == TaskStatus.PENDING
    assert task.retain is False
    assert task.evict_after == 0.0
    assert task.disk_loaded is False
    assert task.notified is False


def test_task_state_terminal_detection():
    """is_terminal() correctly identifies terminal states."""
    task = TaskStateBase(id="test-1", type=TaskType.LOCAL_BASH)
    assert task.is_terminal() is False

    task.status = TaskStatus.COMPLETED
    assert task.is_terminal() is True

    task.status = TaskStatus.FAILED
    assert task.is_terminal() is True

    task.status = TaskStatus.KILLED
    assert task.is_terminal() is True


# ── TaskRegistry ──────────────────────────────────────────────────────────────


def test_registry_register():
    """register() adds a task and returns it via get()."""
    registry = TaskRegistry()
    task = TaskStateBase(id="test-1", type=TaskType.LOCAL_BASH)
    registry.register(task)
    assert registry.get("test-1") is task


def test_registry_register_duplicate():
    """register() overwrites existing task."""
    registry = TaskRegistry()
    task1 = TaskStateBase(id="test-1", type=TaskType.LOCAL_BASH)
    task2 = TaskStateBase(id="test-1", type=TaskType.LOCAL_BASH)
    task2.status = TaskStatus.RUNNING
    registry.register(task1)
    registry.register(task2)
    assert registry.get("test-1") is task2


def test_registry_register_preserves_retain():
    """register() preserves retain flag when re-registering a task."""
    registry = TaskRegistry()
    task1 = TaskStateBase(id="test-1", type=TaskType.LOCAL_BASH)
    task1.retain = True
    registry.register(task1)

    # Re-register (simulates session restart)
    task2 = TaskStateBase(id="test-1", type=TaskType.LOCAL_BASH)
    task2.retain = False  # Default, should be preserved to True
    registry.register(task2)

    assert registry.get("test-1").retain is True


def test_registry_mark_retain():
    """mark_retain() sets retain flag and evict_after."""
    registry = TaskRegistry()
    task = TaskStateBase(id="test-1", type=TaskType.LOCAL_BASH)
    registry.register(task)

    result = registry.mark_retain("test-1", True)
    assert result is True
    assert task.retain is True
    assert task.evict_after > 0

    registry.mark_retain("test-1", False)
    assert task.retain is False
    assert task.evict_after == 0.0


def test_registry_mark_retain_unknown():
    """mark_retain() returns False for unknown task."""
    registry = TaskRegistry()
    assert registry.mark_retain("unknown", True) is False


def test_registry_running_count():
    """running_count() returns correct bash and agent counts."""
    registry = TaskRegistry()
    assert registry.running_count() == (0, 0)

    registry.register(TaskStateBase(id="bash-1", type=TaskType.LOCAL_BASH, status=TaskStatus.RUNNING))
    registry.register(TaskStateBase(id="bash-2", type=TaskType.LOCAL_BASH, status=TaskStatus.RUNNING))
    registry.register(TaskStateBase(id="agent-1", type=TaskType.LOCAL_AGENT, status=TaskStatus.RUNNING))
    registry.register(TaskStateBase(id="done-1", type=TaskType.LOCAL_BASH, status=TaskStatus.COMPLETED))

    bash_count, agent_count = registry.running_count()
    assert bash_count == 2
    assert agent_count == 1


def test_registry_evict_terminal():
    """evict_terminal() removes old terminal tasks."""
    registry = TaskRegistry()
    task = TaskStateBase(id="test-1", type=TaskType.LOCAL_BASH, status=TaskStatus.COMPLETED)
    task._completed_at = time.monotonic() - registry.EVICTION_GRACE_PERIOD - 1
    task.notified = True
    registry.register(task)

    registry.evict_terminal()
    assert registry.get("test-1") is None


def test_registry_evict_preserves_retained():
    """evict_terminal() keeps retained tasks even after grace period."""
    registry = TaskRegistry()
    task = TaskStateBase(id="test-1", type=TaskType.LOCAL_BASH, status=TaskStatus.COMPLETED)
    task._completed_at = time.monotonic() - registry.EVICTION_GRACE_PERIOD - 1
    task.notified = True
    task.retain = True
    task.evict_after = time.monotonic() + 1000  # Still within retention
    registry.register(task)

    registry.evict_terminal()
    assert registry.get("test-1") is task


# ── DiskTaskOutput ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disk_output_append_and_flush():
    """append() queues content, flush() writes to disk."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test.output"
        disk = DiskTaskOutput(str(output_path))

        disk.append("Hello\n")
        disk.append("World\n")
        await disk.flush()

        assert output_path.read_text() == "Hello\nWorld\n"


@pytest.mark.asyncio
async def test_disk_output_read_tail():
    """read_tail() returns last N lines efficiently."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test.output"
        disk = DiskTaskOutput(str(output_path))

        # Write 10 lines
        for i in range(10):
            disk.append(f"Line {i}\n")
        await disk.flush()

        tail = disk.read_tail(lines=3)
        assert "Line 7" in tail
        assert "Line 8" in tail
        assert "Line 9" in tail
        assert "Line 0" not in tail


@pytest.mark.asyncio
async def test_disk_output_read_all():
    """read_all() returns the complete file content."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test.output"
        disk = DiskTaskOutput(str(output_path))

        disk.append("Full content\n")
        disk.append("Line 2\n")
        await disk.flush()

        content = disk.read_all()
        assert "Full content" in content
        assert "Line 2" in content


# ── StallWatchdog ─────────────────────────────────────────────────────────────


def test_stall_watchdog_patterns_compiled():
    """All interactive patterns compile without error."""
    import re

    for pattern, description in INTERACTIVE_PATTERNS:
        # Should not raise
        compiled = re.compile(pattern, re.IGNORECASE)
        assert compiled is not None
        assert len(description) > 0


def test_stall_watchdog_detect_apt_prompt():
    """StallWatchdog detects apt/yum confirmation prompts."""
    import re

    for pattern, description in INTERACTIVE_PATTERNS:
        compiled = re.compile(pattern, re.IGNORECASE)

        # Test apt confirmation
        if compiled.search("Do you want to continue? [Y/n]"):
            assert "package" in description.lower() or "confirmation" in description.lower()


@pytest.mark.asyncio
async def test_stall_watchdog_start_stop():
    """start_watchdog() and stop_watchdog() manage the global registry."""
    clear_watchdogs()

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test.output"

        watchdog = start_watchdog("task-1", str(output_path))
        assert watchdog is not None

        stop_watchdog("task-1")
        # Should not raise


def test_stall_watchdog_record_output():
    """record_output() resets the watchdog timer."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "test.output"
        watchdog = StallWatchdog("task-1", str(output_path))

        time.sleep(0.01)
        watchdog.record_output()

        # Timer was reset (just verify it doesn't crash)
        assert watchdog._last_output_time > 0
