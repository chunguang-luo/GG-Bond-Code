"""Command semantic classification for BashTool.

Mirrors Claude Code's BASH_SEARCH_COMMANDS / BASH_READ_COMMANDS /
BASH_LIST_COMMANDS / BASH_SILENT_COMMANDS sets from BashTool.tsx:60-72.

Semantic classification drives two things:
1. UI display — search commands show "Searched N files", silent commands
   show "Done" instead of "(No output)"
2. Read-only auto-allow — only search/read/list commands can be
   considered for automatic permission grants
"""

from __future__ import annotations

import re
from enum import Enum


class CommandSemantic(Enum):
    """Semantic category of a bash command."""
    SEARCH = "search"        # find, grep, rg, ag, ...
    READ = "read"           # cat, head, tail, wc, ...
    LIST = "list"           # ls, tree, du
    SILENT = "silent"       # mv, cp, rm, mkdir, ... (no stdout on success)
    NEUTRAL = "neutral"     # echo, printf, true, false, :
    DESTRUCTIVE = "destructive"  # Any command not in safe sets
    UNKNOWN = "unknown"


# --- Command sets (mirrors BashTool.tsx:60-72) ---

BASH_SEARCH_COMMANDS: frozenset[str] = frozenset({
    "find", "grep", "rg", "ag", "ack", "locate", "which", "whereis",
})

BASH_READ_COMMANDS: frozenset[str] = frozenset({
    "cat", "head", "tail", "less", "more",
    "wc", "stat", "file", "strings",
    "jq", "awk", "cut", "sort", "uniq", "tr",
})

BASH_LIST_COMMANDS: frozenset[str] = frozenset({"ls", "tree", "du"})

BASH_SILENT_COMMANDS: frozenset[str] = frozenset({
    "mv", "cp", "rm", "mkdir", "rmdir", "chmod", "chown",
    "chgrp", "touch", "ln", "cd", "export", "unset", "wait",
})

BASH_NEUTRAL_COMMANDS: frozenset[str] = frozenset({
    "echo", "printf", "true", "false", ":",
})

# Collapsible semantics — these categories can be collapsed in UI
COLLAPSIBLE_SEMANTICS: frozenset[CommandSemantic] = frozenset({
    CommandSemantic.SEARCH, CommandSemantic.READ, CommandSemantic.LIST,
})

# All safe (non-destructive) semantic categories
SAFE_SEMANTICS: frozenset[CommandSemantic] = COLLAPSIBLE_SEMANTICS | frozenset({
    CommandSemantic.SILENT, CommandSemantic.NEUTRAL,
})


def _extract_base_command(token: str) -> str:
    """Extract the base command name from a token.

    Handles paths like /usr/bin/git -> git.
    """
    return token.rsplit("/", maxsplit=1)[-1] if "/" in token else token


def classify_command(command: str) -> CommandSemantic:
    """Classify a single command (no pipes/&&) by semantic category.

    Returns the most specific category for the command.
    """
    stripped = command.strip()
    if not stripped:
        return CommandSemantic.UNKNOWN

    # Split to get the first token (base command)
    tokens = stripped.split()
    if not tokens:
        return CommandSemantic.UNKNOWN

    base = _extract_base_command(tokens[0])

    if base in BASH_SEARCH_COMMANDS:
        return CommandSemantic.SEARCH
    if base in BASH_READ_COMMANDS:
        return CommandSemantic.READ
    if base in BASH_LIST_COMMANDS:
        return CommandSemantic.LIST
    if base in BASH_SILENT_COMMANDS:
        return CommandSemantic.SILENT
    if base in BASH_NEUTRAL_COMMANDS:
        return CommandSemantic.NEUTRAL
    return CommandSemantic.UNKNOWN


# Regex for splitting compound commands on && and ||
# Does NOT split inside quotes (simplified — good enough for classification)
_COMPOUND_SPLIT_RE = re.compile(r"\s*(?:&&|\|\|)\s*")
_PIPE_SPLIT_RE = re.compile(r"\s*\|\s*")


def classify_pipeline(command: str) -> CommandSemantic:
    """Classify a full command pipeline.

    A pipeline is considered collapsible (search/read/list) only if
    ALL non-neutral sub-commands belong to the same collapsible category.

    Mirrors isSearchOrReadBashCommand() in BashTool.tsx:59-172.
    """
    # Split on pipe operators
    parts = _PIPE_SPLIT_RE.split(command)

    collapsible_categories: set[CommandSemantic] = set()

    for part in parts:
        # Further split on && and ||
        subcmds = _COMPOUND_SPLIT_RE.split(part)
        for subcmd in subcmds:
            subcmd = subcmd.strip()
            if not subcmd:
                continue
            sem = classify_command(subcmd)
            if sem == CommandSemantic.NEUTRAL:
                continue  # Neutral commands don't affect classification
            if sem in COLLAPSIBLE_SEMANTICS:
                collapsible_categories.add(sem)
            else:
                # Any non-collapsible, non-neutral command -> DESTRUCTIVE
                return CommandSemantic.DESTRUCTIVE

    if not collapsible_categories:
        # All commands were neutral or empty
        return CommandSemantic.NEUTRAL

    # If all collapsible commands are in the same category, use that
    if len(collapsible_categories) == 1:
        return collapsible_categories.pop()

    # Mixed collapsible categories — search takes precedence
    if CommandSemantic.SEARCH in collapsible_categories:
        return CommandSemantic.SEARCH
    return CommandSemantic.UNKNOWN


def is_silent_command(command: str) -> bool:
    """Check if a command typically produces no stdout on success.

    Used to display "Done" instead of "(No output)" in the UI.
    """
    tokens = command.strip().split()
    if not tokens:
        return False
    base = _extract_base_command(tokens[0])
    return base in BASH_SILENT_COMMANDS
