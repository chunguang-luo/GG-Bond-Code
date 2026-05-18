"""Unit tests for FileStateCache."""

from __future__ import annotations

import time

from next_code.compact.file_cache import FileStateCache, FileStateEntry


class TestFileStateEntry:
    """Tests for FileStateEntry dataclass."""

    def test_defaults(self):
        entry = FileStateEntry(
            path="/tmp/test.py",
            content="hello",
            timestamp=1.0,
        )
        assert entry.offset is None
        assert entry.limit is None
        assert entry.is_partial_view is False
        assert entry.was_edited is False

    def test_partial_view_with_offset(self):
        entry = FileStateEntry(
            path="/tmp/test.py",
            content="hello",
            timestamp=1.0,
            offset=100,
            is_partial_view=True,
        )
        assert entry.is_partial_view is True

    def test_partial_view_with_limit(self):
        entry = FileStateEntry(
            path="/tmp/test.py",
            content="hello",
            timestamp=1.0,
            limit=50,
            is_partial_view=True,
        )
        assert entry.is_partial_view is True


class TestFileStateCacheRecordRead:
    """Tests for record_read."""

    def test_record_read_full_file(self):
        cache = FileStateCache()
        cache.record_read("/tmp/a.py", "content of a")
        assert cache.entry_count == 1
        assert cache.size_bytes > 0

    def test_record_read_partial_view(self):
        cache = FileStateCache()
        cache.record_read("/tmp/a.py", "content of a", offset=1, limit=50)
        entry = cache._entries["/tmp/a.py"]
        assert entry.is_partial_view is True
        assert entry.offset == 1
        assert entry.limit == 50

    def test_record_read_full_no_partial(self):
        cache = FileStateCache()
        cache.record_read("/tmp/a.py", "content of a")
        entry = cache._entries["/tmp/a.py"]
        assert entry.is_partial_view is False

    def test_record_read_updates_existing(self):
        cache = FileStateCache()
        cache.record_read("/tmp/a.py", "old content")
        cache.record_read("/tmp/a.py", "new content")
        assert cache.entry_count == 1
        assert cache._entries["/tmp/a.py"].content == "new content"

    def test_record_read_skips_very_large_files(self):
        cache = FileStateCache()
        # Create content larger than 5MB
        large_content = "x" * (6 * 1024 * 1024)
        cache.record_read("/tmp/big.py", large_content)
        assert cache.entry_count == 0


class TestFileStateCacheRecordEdit:
    """Tests for record_edit and record_write."""

    def test_record_edit_marks_full_view(self):
        cache = FileStateCache()
        cache.record_read("/tmp/a.py", "original", offset=1, limit=10)
        assert cache._entries["/tmp/a.py"].is_partial_view is True

        cache.record_edit("/tmp/a.py", "modified content")
        assert cache._entries["/tmp/a.py"].is_partial_view is False
        assert cache._entries["/tmp/a.py"].was_edited is True

    def test_record_edit_creates_entry_if_missing(self):
        cache = FileStateCache()
        cache.record_edit("/tmp/a.py", "new file content")
        assert cache.entry_count == 1
        assert cache._entries["/tmp/a.py"].was_edited is True
        assert cache._entries["/tmp/a.py"].is_partial_view is False

    def test_record_write_same_as_edit(self):
        cache = FileStateCache()
        cache.record_write("/tmp/a.py", "written content")
        assert cache.entry_count == 1
        assert cache._entries["/tmp/a.py"].was_edited is True


class TestFileStateCacheCanEdit:
    """Tests for can_edit."""

    def test_can_edit_unknown_file(self):
        cache = FileStateCache()
        allowed, reason = cache.can_edit("/tmp/unknown.py")
        assert allowed is True
        assert "not recently read" in reason

    def test_can_edit_full_view(self):
        cache = FileStateCache()
        cache.record_read("/tmp/a.py", "content")
        allowed, reason = cache.can_edit("/tmp/a.py")
        assert allowed is True
        assert reason == "OK"

    def test_cannot_edit_partial_view(self):
        cache = FileStateCache()
        cache.record_read("/tmp/a.py", "content", offset=1, limit=50)
        allowed, reason = cache.can_edit("/tmp/a.py")
        assert allowed is False
        assert "partially viewed" in reason

    def test_can_edit_after_edit(self):
        cache = FileStateCache()
        cache.record_read("/tmp/a.py", "content", offset=1, limit=50)
        cache.record_edit("/tmp/a.py", "modified")
        allowed, reason = cache.can_edit("/tmp/a.py")
        assert allowed is True


class TestFileStateCacheGetRecent:
    """Tests for get_recent."""

    def test_get_recent_ordered_by_timestamp(self):
        cache = FileStateCache()
        cache.record_read("/tmp/a.py", "a")
        time.sleep(0.01)
        cache.record_read("/tmp/b.py", "b")
        time.sleep(0.01)
        cache.record_read("/tmp/c.py", "c")

        recent = cache.get_recent(2)
        assert len(recent) == 2
        assert recent[0].path == "/tmp/c.py"
        assert recent[1].path == "/tmp/b.py"

    def test_get_recent_limited(self):
        cache = FileStateCache()
        for i in range(10):
            cache.record_read(f"/tmp/{i}.py", f"content {i}")
            time.sleep(0.001)

        recent = cache.get_recent(5)
        assert len(recent) == 5

    def test_get_recent_empty(self):
        cache = FileStateCache()
        recent = cache.get_recent(5)
        assert recent == []


class TestFileStateCacheLRU:
    """Tests for LRU eviction."""

    def test_evict_on_max_entries(self):
        cache = FileStateCache(max_entries=3)
        cache.record_read("/tmp/a.py", "a")
        cache.record_read("/tmp/b.py", "b")
        cache.record_read("/tmp/c.py", "c")
        cache.record_read("/tmp/d.py", "d")  # Should evict a
        assert cache.entry_count == 3
        assert "/tmp/a.py" not in cache._entries
        assert "/tmp/d.py" in cache._entries

    def test_evict_on_max_size(self):
        cache = FileStateCache(max_entries=100, max_size_bytes=100)
        cache.record_read("/tmp/a.py", "a" * 50)  # ~50 bytes
        cache.record_read("/tmp/b.py", "b" * 50)  # ~50 bytes, total ~100
        cache.record_read("/tmp/c.py", "c" * 50)  # Should evict a
        assert "/tmp/a.py" not in cache._entries
        assert "/tmp/c.py" in cache._entries


class TestFileStateCacheClear:
    """Tests for clear."""

    def test_clear_empties_cache(self):
        cache = FileStateCache()
        cache.record_read("/tmp/a.py", "a")
        cache.record_read("/tmp/b.py", "b")
        cache.clear()
        assert cache.entry_count == 0
        assert cache.size_bytes == 0


class TestFileStateCacheClone:
    """Tests for clone."""

    def test_clone_creates_independent_copy(self):
        cache = FileStateCache()
        cache.record_read("/tmp/a.py", "original")

        clone = cache.clone()
        assert clone.entry_count == 1

        # Modify clone — should not affect original
        clone.record_edit("/tmp/a.py", "modified")
        assert cache._entries["/tmp/a.py"].content == "original"
        assert clone._entries["/tmp/a.py"].content == "modified"

    def test_clone_preserves_limits(self):
        cache = FileStateCache(max_entries=10, max_size_bytes=1000)
        clone = cache.clone()
        assert clone._max_entries == 10
        assert clone._max_size_bytes == 1000
