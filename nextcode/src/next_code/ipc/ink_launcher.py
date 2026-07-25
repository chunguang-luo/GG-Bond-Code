"""Ink Launcher — spawn and manage the Ink (Node.js) child process.

The Python Core process spawns the Ink frontend as a child process,
passing IPC pipe file descriptors via pass_fds + environment variables.

Lifecycle:
    1. launch() — create pipes, spawn node process, return pipe fds
    2. is_alive() — check process health
    3. shutdown() — graceful shutdown (SIGTERM → SIGKILL)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _find_frontend_bundle() -> Path | None:
    """Locate the Ink frontend bundle (dist/index.js)."""
    pkg_dir = Path(__file__).parent
    bundle = pkg_dir / "frontend" / "dist" / "index.js"
    if bundle.exists():
        return bundle

    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "frontend" / "dist" / "index.js"
        if candidate.exists():
            return candidate

    env_path = os.environ.get("NEXTCODE_INK_BUNDLE")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    return None


def _find_node() -> str | None:
    """Find the Node.js executable."""
    env_node = os.environ.get("NEXTCODE_NODE_PATH")
    if env_node and Path(env_node).exists():
        return env_node

    return shutil.which("node")


def _set_inheritable(fd: int) -> None:
    """Mark a file descriptor as inheritable by child processes."""
    if sys.platform == "win32":
        import msvcrt
        # Windows: set the handle inheritable
        handle = msvcrt.get_osfhandle(fd)
        import _winapi
        _winapi.SetHandleInformation(handle, 1, 1)  # HANDLE_FLAG_INHERIT = 1
    else:
        # Unix: fds are inherited by default, ensure CLOEXEC is not set
        import fcntl
        flags = fcntl.fcntl(fd, fcntl.F_GETFD)
        flags &= ~fcntl.FD_CLOEXEC
        fcntl.fcntl(fd, fcntl.F_SETFD, flags)


class InkLauncher:
    """Manage the Ink (Node.js) child process lifecycle."""

    def __init__(self, session_id: str | None = None) -> None:
        self.session_id = session_id or f"nextcode-{os.getpid()}"
        self.process: subprocess.Popen | None = None
        self._node_path: str | None = None
        self._bundle_path: Path | None = None
        self._master_fd: int | None = None  # PTY master fd
        self._slave_fd: int | None = None   # PTY slave fd

        # Pipe fds (set after launch)
        self.tx_fd: int | None = None  # Python writes IPC → Node.js reads
        self.rx_fd: int | None = None  # Python reads IPC ← Node.js writes

    def can_launch(self) -> tuple[bool, str]:
        """Check if Ink can be launched."""
        self._node_path = _find_node()
        if self._node_path is None:
            return False, "Node.js not found (need >= 18)"

        self._bundle_path = _find_frontend_bundle()
        if self._bundle_path is None:
            return False, "Ink frontend bundle not found"

        return True, "OK"

    async def launch(self, timeout: float = 10.0) -> bool:
        """Spawn the Ink process and return True if successful.

        Creates two pipes for bidirectional IPC:
        - py_to_node: Python → Node.js (IPC_TX)
        - node_to_py: Node.js → Python (IPC_RX)

        The child process inherits:
        - fd 3: read end of py_to_node pipe (IPC messages from Python)
        - fd 4: write end of node_to_py pipe (IPC messages to Python)
        """
        if self._node_path is None or self._bundle_path is None:
            can, reason = self.can_launch()
            if not can:
                logger.warning("Ink: cannot launch: %s", reason)
                return False

        # Create IPC pipes
        py_to_node_r, py_to_node_w = os.pipe()  # Python writes to w, Node reads from r
        node_to_py_r, node_to_py_w = os.pipe()  # Node writes to w, Python reads from r

        # Mark child's fds as inheritable
        _set_inheritable(py_to_node_r)
        _set_inheritable(node_to_py_w)

        # Build command
        cmd = [
            self._node_path,
            str(self._bundle_path),
            "--session-id", self.session_id,
        ]

        # Environment with IPC fd hints
        env = os.environ.copy()
        env["NEXTCODE_IPC_RX_FD"] = str(py_to_node_r)  # Node.js reads IPC from Python
        env["NEXTCODE_IPC_TX_FD"] = str(node_to_py_w)  # Node.js writes IPC to Python

        logger.info("Ink: launching: %s", " ".join(cmd))

        try:
            stdin_target = sys.stdin
            stdout_target = sys.stdout
            stderr_target = sys.stderr
            self._master_fd = None
            self._slave_fd = None

            if not sys.stdin.isatty():
                import pty as _pty
                master_fd, slave_fd = _pty.openpty()
                stdin_target = slave_fd
                stdout_target = slave_fd
                stderr_target = slave_fd
                self._master_fd = master_fd
                self._slave_fd = slave_fd
                logger.info("Ink: using PTY for non-TTY environment")

            self.process = subprocess.Popen(
                cmd,
                stdin=stdin_target,
                stdout=stdout_target,
                stderr=subprocess.PIPE,  # Capture stderr for drain+logging
                pass_fds=(py_to_node_r, node_to_py_w),
                env=env,
                start_new_session=True,  # isolate from Ctrl+C: SIGINT → Ink only, Python cleans up gracefully
            )

            # Close slave fd in parent
            if self._master_fd is not None:
                os.close(slave_fd)

        except OSError as e:
            logger.warning("Ink: failed to spawn: %s", e)
            os.close(py_to_node_r)
            os.close(py_to_node_w)
            os.close(node_to_py_r)
            os.close(node_to_py_w)
            return False

        # Close child's ends in parent (parent keeps py_to_node_w and node_to_py_r)
        os.close(py_to_node_r)
        os.close(node_to_py_w)

        # Store parent-side fds
        self.tx_fd = py_to_node_w  # Parent writes IPC → child reads
        self.rx_fd = node_to_py_r  # Parent reads IPC ← child writes

        # Wait briefly to check if the process exits immediately
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

        logger.info("Ink: process started (pid=%d, tx_fd=%d, rx_fd=%d)",
                     self.process.pid, self.tx_fd, self.rx_fd)
        return True

    @property
    def is_alive(self) -> bool:
        """Check if the Ink process is still running."""
        return self.process is not None and self.process.poll() is None

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Gracefully shut down the Ink process.

        Sequence: SIGTERM → wait 2s → SIGKILL
        """
        if self.process is None:
            return

        if not self.is_alive:
            logger.info("Ink: process already exited (code=%d)", self.process.returncode)
            return

        logger.info("Ink: shutting down (pid=%d)", self.process.pid)

        try:
            self.process.terminate()
        except OSError:
            pass

        for _ in range(int(timeout / 0.2)):
            if not self.is_alive:
                logger.info("Ink: process exited cleanly")
                return
            await asyncio.sleep(0.2)

        logger.warning("Ink: force killing process")
        try:
            self.process.kill()
        except OSError:
            pass

        # Clean up pipe fds
        for fd in (self.tx_fd, self.rx_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self.tx_fd = None
        self.rx_fd = None

        # Clean up PTY
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

    def get_stderr(self) -> str:
        """Read and return any stderr output from the Ink process."""
        if self.process is None:
            return ""
        if self.process.stderr is None:
            return ""
        try:
            return self.process.stderr.read().decode("utf-8", errors="replace")[:1000]
        except Exception:
            return ""
