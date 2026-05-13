"""Ink Launcher — spawn and manage the Ink (Node.js) child process.

The Python Core process spawns the Ink frontend as a child process,
passing the socket path and session ID as command-line arguments.

Lifecycle:
    1. launch() — spawn node process, wait for ready signal
    2. is_alive() — check process health
    3. shutdown() — graceful shutdown (message → SIGTERM → SIGKILL)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _find_frontend_bundle() -> Path | None:
    """Locate the Ink frontend bundle (dist/index.js).

    Search order:
    1. Relative to the Python package (src/gg_bond_code/frontend/dist/)
    2. Walk up from __file__ to find a gg-bond-code/frontend/dist/ directory
    3. BUNDLED_PATH env var
    """
    # 1. Relative to the Python package (pip-installed layout)
    pkg_dir = Path(__file__).parent
    bundle = pkg_dir / "frontend" / "dist" / "index.js"
    if bundle.exists():
        return bundle

    # 2. Walk up from this file to find project root with frontend/
    #    Typical: src/gg_bond_code/ipc/ink_launcher.py
    #    Target:  gg-bond-code/frontend/dist/index.js
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "frontend" / "dist" / "index.js"
        if candidate.exists():
            return candidate

    # 3. Environment variable override
    env_path = os.environ.get("GGBOND_INK_BUNDLE")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    return None


def _find_node() -> str | None:
    """Find the Node.js executable.

    Search order:
    1. GGBOND_NODE_PATH env var
    2. PATH lookup for 'node'
    """
    env_node = os.environ.get("GGBOND_NODE_PATH")
    if env_node and Path(env_node).exists():
        return env_node

    return shutil.which("node")


class InkLauncher:
    """Manage the Ink (Node.js) child process lifecycle."""

    def __init__(self, socket_path: str, session_id: str | None = None) -> None:
        self.socket_path = socket_path
        self.session_id = session_id or f"ggbond-{os.getpid()}"
        self.process: subprocess.Popen | None = None
        self._node_path: str | None = None
        self._bundle_path: Path | None = None
        self._master_fd: int | None = None  # PTY master fd (for non-TTY environments)

    def can_launch(self) -> tuple[bool, str]:
        """Check if Ink can be launched.

        Returns (can_launch, reason) tuple.
        """
        # Check Node.js
        self._node_path = _find_node()
        if self._node_path is None:
            return False, "Node.js not found (need >= 18)"

        # Check frontend bundle
        self._bundle_path = _find_frontend_bundle()
        if self._bundle_path is None:
            return False, "Ink frontend bundle not found"

        return True, "OK"

    async def launch(self, timeout: float = 10.0) -> bool:
        """Spawn the Ink process and return True if successful.

        Args:
            timeout: Seconds to wait for the process to start.
        """
        if self._node_path is None or self._bundle_path is None:
            can, reason = self.can_launch()
            if not can:
                logger.warning("Ink: cannot launch: %s", reason)
                return False

        cmd = [
            self._node_path,
            str(self._bundle_path),
            "--socket", self.socket_path,
            "--session-id", self.session_id,
        ]

        logger.info("Ink: launching: %s", " ".join(cmd))

        try:
            # Ink requires direct access to the terminal for rendering.
            # It needs:
            #   - stdin: TTY for raw mode input
            #   - stdout: terminal for ANSI rendering (alt screen, cursor control)
            #   - stderr: terminal for error output
            #
            # Strategy: if Python's stdin is a TTY (interactive terminal),
            # pass stdin/stdout/stderr through so Ink owns the terminal.
            # Otherwise, create a PTY pair for Ink.
            stdin_target = sys.stdin
            stdout_target = sys.stdout
            stderr_target = sys.stderr
            self._master_fd = None

            if not sys.stdin.isatty():
                import pty as _pty
                master_fd, slave_fd = _pty.openpty()
                stdin_target = slave_fd
                stdout_target = slave_fd
                stderr_target = slave_fd
                self._master_fd = master_fd
                logger.info("Ink: using PTY for non-TTY environment")

            self.process = subprocess.Popen(
                cmd,
                stdin=stdin_target,
                stdout=stdout_target,
                stderr=stderr_target,
            )

            # Close slave fd in parent after Popen has inherited it
            if self._master_fd is not None:
                os.close(slave_fd)
        except OSError as e:
            logger.warning("Ink: failed to spawn: %s", e)
            return False

        # Wait briefly to check if the process exits immediately
        # (e.g., missing module, syntax error)
        await asyncio.sleep(0.2)
        if not self.is_alive:
            stderr_output = ""
            if self.process.stderr is not None:
                try:
                    stderr_output = self.process.stderr.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
            logger.warning("Ink: process exited immediately. stderr: %s", stderr_output)
            return False

        logger.info("Ink: process started (pid=%d)", self.process.pid)
        return True

    @property
    def is_alive(self) -> bool:
        """Check if the Ink process is still running."""
        return self.process is not None and self.process.poll() is None

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Gracefully shut down the Ink process.

        Sequence: wait for timeout → SIGTERM → wait 2s → SIGKILL
        """
        if self.process is None:
            return

        if not self.is_alive:
            logger.info("Ink: process already exited (code=%d)", self.process.returncode)
            return

        logger.info("Ink: shutting down (pid=%d)", self.process.pid)

        # Try SIGTERM first
        try:
            self.process.terminate()
        except OSError:
            pass

        # Wait for process to exit
        for _ in range(int(timeout / 0.2)):
            if not self.is_alive:
                logger.info("Ink: process exited cleanly")
                return
            await asyncio.sleep(0.2)

        # Force kill
        logger.warning("Ink: force killing process")
        try:
            self.process.kill()
        except OSError:
            pass

        # Clean up PTY if we created one
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

    def get_stderr(self) -> str:
        """Read and return any stderr output from the Ink process.

        Only works when stderr was captured (not inherited).
        """
        if self.process is None:
            return ""
        if self.process.stderr is None:
            # stderr was inherited, can't read it
            return ""
        try:
            return self.process.stderr.read().decode("utf-8", errors="replace")[:1000]
        except Exception:
            return ""
