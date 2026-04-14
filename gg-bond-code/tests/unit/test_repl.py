"""Tests for repl.py — REPL initialization and permission callback wiring."""

from gg_bond_code.repl import REPL
from gg_bond_code.permissions.manager import PermissionDecision


def test_repl_has_permission_callback():
    """REPL wires up a permission callback to QueryRunner."""
    repl = REPL(model="deepseek-chat")
    assert repl.runner._permission_callback is not None


def test_repl_format_params():
    """_format_params truncates long values."""
    params = {"key": "short", "long_key": "x" * 100}
    result = REPL._format_params(params)
    assert "short" in result
    assert "..." in result


def test_repl_format_params_empty():
    """_format_params handles empty dict."""
    assert REPL._format_params({}) == ""
