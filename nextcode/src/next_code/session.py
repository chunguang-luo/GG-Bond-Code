"""Session persistence — save/load/resume conversation sessions.

Sessions are stored as JSON files in <project_root>/.nextcode/sessions/.
Each session file contains the full message history and metadata.

Session ID format: YYYYMMDD_HHMMSS_XXXXXX (timestamp + 6-char random hex)
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Session ID ────────────────────────────────────────────────────────────────

def generate_session_id() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    rand = secrets.token_hex(3)
    return f"{ts}_{rand}"


# ── Session data class ────────────────────────────────────────────────────────

@dataclass
class SessionMeta:
    session_id: str = ""
    title: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0
    model: str = ""
    cwd: str = ""
    turn_count: int = 0
    user_message_count: int = 0
    tool_call_count: int = 0


def _sessions_dir(project_root: str) -> Path:
    return Path(project_root) / ".nextcode" / "sessions"


def _ensure_sessions_dir(project_root: str) -> Path:
    d = _sessions_dir(project_root)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Save ──────────────────────────────────────────────────────────────────────

def save_session(
    project_root: str,
    session_id: str,
    messages: list[dict[str, Any]],
    meta: SessionMeta,
) -> str | None:
    """Save session to disk. Returns the file path on success, None on failure."""
    if not session_id or not messages:
        return None

    meta.ended_at = time.time()

    try:
        d = _ensure_sessions_dir(project_root)
        path = d / f"{session_id}.json"
        data: dict[str, Any] = {
            "session_id": session_id,
            "title": meta.title,
            "started_at": meta.started_at,
            "ended_at": meta.ended_at,
            "model": meta.model,
            "cwd": meta.cwd,
            "turn_count": meta.turn_count,
            "user_message_count": meta.user_message_count,
            "tool_call_count": meta.tool_call_count,
            "messages": messages,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Session saved: %s (%d messages)", path, len(messages))
        return str(path)
    except Exception:
        logger.exception("Failed to save session %s", session_id)
        return None


# ── Load ──────────────────────────────────────────────────────────────────────

def load_session(session_id: str) -> dict[str, Any] | None:
    """Load a session by ID. Searches project root's .nextcode/sessions/."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / ".nextcode" / "sessions" / f"{session_id}.json"
        if candidate.exists():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.exception("Failed to load session %s", session_id)
                return None

    # Also search in project_root from the current setup if available
    try:
        from .state.store import Store
        store = Store()
        project_root = store.get("project_root", "")
        if project_root:
            candidate = Path(str(project_root)) / ".nextcode" / "sessions" / f"{session_id}.json"
            if candidate.exists():
                return json.loads(candidate.read_text(encoding="utf-8"))
    except Exception:
        pass

    return None


def find_session(session_id: str, project_root: str) -> Path | None:
    """Find a session file by ID. Returns the Path or None."""
    candidate = _sessions_dir(project_root) / f"{session_id}.json"
    return candidate if candidate.exists() else None


# ── List ──────────────────────────────────────────────────────────────────────

def list_sessions(project_root: str) -> list[dict[str, Any]]:
    """List all saved sessions with metadata (sorted newest first)."""
    d = _sessions_dir(project_root)
    if not d.exists():
        return []

    sessions: list[dict[str, Any]] = []
    for f in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sessions.append(data)
        except (json.JSONDecodeError, OSError):
            pass
    return sessions


# ── Exit summary ──────────────────────────────────────────────────────────────

def format_exit_summary(
    session_id: str,
    title: str,
    started_at: float,
    user_message_count: int,
    tool_call_count: int,
    total_messages: int,
) -> str:
    duration_sec = time.time() - started_at
    duration_str = _format_duration(duration_sec)

    title_str = title or "(no messages)"
    # Truncate long titles
    if len(title_str) > 60:
        title_str = title_str[:57] + "..."

    lines = [
        "",
        "\033[1mResume this session with:\033[0m",
        f"  \033[36mnextcode --resume {session_id}\033[0m",
        "",
        f"  Session:        {session_id}",
        f"  Title:          {title_str}",
        f"  Duration:       {duration_str}",
        f"  Messages:       {total_messages} ({user_message_count} user, {tool_call_count} tool calls)",
        "",
    ]
    return "\n".join(lines)


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds / 60)
    if minutes < 60:
        return f"{minutes}m {int(seconds % 60)}s"
    hours = minutes // 60
    remaining_min = minutes % 60
    if hours < 24:
        return f"{hours}h {remaining_min}m {int(seconds % 60)}s"
    days = hours // 24
    remaining_h = hours % 24
    return f"{days}d {remaining_h}h {remaining_min}m"


def extract_title(messages: list[dict[str, Any]]) -> str:
    """Extract a session title from the first user message."""
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                text = content.strip()
            elif isinstance(content, list):
                # Anthropic format: list of content blocks
                texts = [b.get("text", "") for b in content if b.get("type") == "text"]
                text = " ".join(texts).strip()
            else:
                continue

            if text:
                # Truncate to ~50 chars, break at word boundary
                if len(text) > 50:
                    text = text[:47].rsplit(" ", 1)[0] + "..."
                return text
    return ""
