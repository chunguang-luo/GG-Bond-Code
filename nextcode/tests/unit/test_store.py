"""Tests for state/store.py — _Store subscribe, onChange, snapshot, equality check."""

from next_code.state.store import _Store, Store, get_store, reset_store


# ── Basic get/set/delete/has ──────────────────────────────────────────

def test_get_set_basic():
    store = _Store()
    store.set("key", "value")
    assert store.get("key") == "value"


def test_get_default():
    store = _Store()
    assert store.get("missing") is None
    assert store.get("missing", 42) == 42


def test_get_none_value():
    """None is a valid stored value, not the same as missing."""
    store = _Store()
    store.set("key", None)
    assert store.get("key") is None
    assert store.has("key") is True


def test_delete():
    store = _Store()
    store.set("key", "value")
    store.delete("key")
    assert store.has("key") is False
    assert store.get("key") is None


def test_delete_missing_is_noop():
    store = _Store()
    store.delete("nonexistent")  # should not raise


def test_has():
    store = _Store()
    assert store.has("key") is False
    store.set("key", 1)
    assert store.has("key") is True


# ── Equality check — skip notification when unchanged ──────────────────

def test_set_same_value_no_notification():
    """Setting the same value should not trigger listeners or onChange."""
    changes = []
    store = _Store(on_change=lambda k, v, o: changes.append((k, v, o)))
    listener_calls = []
    store.subscribe(lambda k, v, o: listener_calls.append((k, v, o)))

    store.set("model", "deepseek-chat")
    assert len(changes) == 1
    assert len(listener_calls) == 1

    # Set same value again — should be skipped
    store.set("model", "deepseek-chat")
    assert len(changes) == 1  # no new call
    assert len(listener_calls) == 1


def test_set_different_value_notifies():
    changes = []
    store = _Store(on_change=lambda k, v, o: changes.append((k, v, o)))

    store.set("model", "deepseek-chat")
    store.set("model", "claude-sonnet")
    assert len(changes) == 2
    assert changes[1] == ("model", "claude-sonnet", "deepseek-chat")


# ── onChange callback ─────────────────────────────────────────────────

def test_on_change_fires_on_set():
    changes = []
    store = _Store(on_change=lambda k, v, o: changes.append((k, v, o)))

    store.set("cwd", "/home/user")
    assert changes == [("cwd", "/home/user", None)]

    store.set("cwd", "/tmp")
    assert changes[-1] == ("cwd", "/tmp", "/home/user")


def test_on_change_none_when_key_new():
    """old_value is None when the key didn't exist before."""
    changes = []
    store = _Store(on_change=lambda k, v, o: changes.append((k, v, o)))

    store.set("new_key", 123)
    assert changes[0][2] is None  # old_value is None


def test_on_change_not_called_when_no_change():
    calls = []
    store = _Store(on_change=lambda k, v, o: calls.append(1))
    store.set("x", 1)
    store.set("x", 1)  # same value
    assert len(calls) == 1


def test_on_change_fires_before_listeners():
    """onChange fires before subscribers — mirrors Claude Code's ordering."""
    order = []
    store = _Store(on_change=lambda k, v, o: order.append("onChange"))
    store.subscribe(lambda k, v, o: order.append("listener"))

    store.set("key", "val")
    assert order == ["onChange", "listener"]


# ── subscribe / unsubscribe ───────────────────────────────────────────

def test_subscribe_receives_changes():
    calls = []
    store = _Store()
    store.subscribe(lambda k, v, o: calls.append((k, v, o)))

    store.set("a", 1)
    store.set("b", 2)
    assert len(calls) == 2
    assert calls[0] == ("a", 1, None)
    assert calls[1] == ("b", 2, None)


def test_subscribe_returns_unsubscribe():
    calls = []
    store = _Store()
    unsub = store.subscribe(lambda k, v, o: calls.append(1))

    store.set("a", 1)
    assert len(calls) == 1

    unsub()
    store.set("b", 2)
    assert len(calls) == 1  # no new call after unsubscribe


def test_multiple_listeners():
    calls_a = []
    calls_b = []
    store = _Store()
    store.subscribe(lambda k, v, o: calls_a.append(k))
    store.subscribe(lambda k, v, o: calls_b.append(k))

    store.set("x", 1)
    assert calls_a == ["x"]
    assert calls_b == ["x"]


def test_listener_set_dedup():
    """Adding the same listener twice should not double-notify."""
    calls = []
    store = _Store()
    fn = lambda k, v, o: calls.append(1)
    store.subscribe(fn)
    store.subscribe(fn)  # duplicate

    store.set("x", 1)
    assert len(calls) == 1  # only once


# ── snapshot — deep copy ──────────────────────────────────────────────

def test_snapshot_returns_deep_copy():
    """Mutating the snapshot must not affect the store."""
    store = _Store()
    store.set("items", [1, 2, 3])

    snap = store.snapshot()
    snap["items"].append(4)

    # Store is unaffected
    assert store.get("items") == [1, 2, 3]
    assert snap["items"] == [1, 2, 3, 4]


def test_snapshot_nested_dict():
    store = _Store()
    store.set("config", {"nested": {"deep": True}})

    snap = store.snapshot()
    snap["config"]["nested"]["deep"] = False

    assert store.get("config")["nested"]["deep"] is True


def test_snapshot_independent_of_later_set():
    store = _Store()
    store.set("key", "old")
    snap = store.snapshot()

    store.set("key", "new")
    assert snap["key"] == "old"


# ── Singleton / module-level API ──────────────────────────────────────

def test_store_singleton():
    """Store() and get_store() return the same instance."""
    assert Store() is get_store()


def test_reset_store():
    """reset_store() creates a fresh singleton."""
    old = Store()
    old.set("key", "value")

    new = reset_store()
    assert new is not old
    assert new.get("key") is None
    assert Store() is new  # global singleton updated


def test_reset_store_with_on_change():
    calls = []
    new = reset_store(on_change=lambda k, v, o: calls.append(k))
    new.set("x", 1)
    assert calls == ["x"]
    # Clean up — reset to default for other tests
    reset_store()


# ── reset() instance method ───────────────────────────────────────────

def test_reset_clears_data():
    """reset() clears all stored data."""
    store = _Store()
    store.set("a", 1)
    store.set("b", 2)
    store.reset()
    assert store.get("a") is None
    assert store.get("b") is None
    assert store.has("a") is False


def test_reset_notifies_listeners():
    """reset() notifies listeners with __reset__ key."""
    notifications = []
    store = _Store()
    store.subscribe(lambda k, v, o: notifications.append(k))

    store.set("x", 1)
    store.reset()
    assert "__reset__" in notifications


def test_reset_passes_old_data_to_listener():
    """reset() passes old data as old_value to listeners."""
    captured = []
    store = _Store()
    store.set("x", 1)
    store.subscribe(lambda k, v, o: captured.append((k, o)))

    store.reset()
    # The listener should receive the old data dict
    assert captured[0][0] == "__reset__"
    assert captured[0][1] == {"x": 1}


def test_reset_keeps_listeners():
    """reset() does not remove listeners."""
    calls = []
    store = _Store()
    store.subscribe(lambda k, v, o: calls.append(k))

    store.reset()
    store.set("new_key", "new_val")
    assert "new_key" in calls


def test_reset_keeps_on_change():
    """reset() does not remove the onChange callback."""
    calls = []
    store = _Store(on_change=lambda k, v, o: calls.append(k))
    store.reset()
    store.set("x", 1)
    assert "x" in calls
