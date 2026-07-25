"""Main orchestrator — Click command definitions + init dispatch."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from .init import init


@click.group(invoke_without_command=True)
@click.option("--version", "-V", is_flag=True, help="Show version.")
@click.option("--print", "print_mode", is_flag=True, help="Non-interactive mode (read from stdin).")
@click.option("--model", default=None, help="Model to use.")
@click.option("--cwd", default=None, help="Working directory.")
@click.option("--resume", "resume_from", default=None, help="Resume a previous session by ID.")
@click.option("--title", "-t", default=None, help="Custom title for this session.")
@click.option("--sessions", "list_sessions_flag", is_flag=True, help="List saved sessions.")
@click.option("--allowed-tools", "allowed_tools", default=None, help="Comma-separated list of allowed tools.")
@click.option("--disallowed-tools", "disallowed_tools", default=None, help="Comma-separated list of disallowed tools.")
@click.option("--mcp-config", "mcp_config", default=None,
              help="Path to MCP server config JSON, or inline JSON.")
@click.option("--permission-mode", "permission_mode", default=None,
              type=click.Choice(["default", "plan", "acceptEdits", "bypassPermissions", "dontAsk"]),
              help="Permission mode for this session.")
@click.pass_context
def cli(ctx: click.Context, version: bool, print_mode: bool, model: str | None,
        cwd: str | None, resume_from: str | None, title: str | None,
        list_sessions_flag: bool,
        allowed_tools: str | None, disallowed_tools: str | None,
        mcp_config: str | None, permission_mode: str | None) -> None:
    """NextCode — AI-powered CLI assistant."""
    ctx.ensure_object(dict)

    if version:
        from . import __version__
        click.echo(f"{__version__} (NextCode)")
        return

    # Run init (memoized — only executes once)
    init()

    # Handle --sessions: list saved sessions and exit
    if list_sessions_flag:
        _list_sessions(cwd or str(Path.cwd()))
        return

    # Store options in context
    ctx.obj["model"] = model
    ctx.obj["cwd"] = cwd or str(Path.cwd())
    ctx.obj["print_mode"] = print_mode
    ctx.obj["resume_from"] = resume_from
    ctx.obj["title"] = title
    ctx.obj["allowed_tools"] = allowed_tools
    ctx.obj["disallowed_tools"] = disallowed_tools
    ctx.obj["mcp_config"] = mcp_config
    ctx.obj["permission_mode"] = permission_mode

    # If no sub-command, launch interactive REPL
    if ctx.invoked_subcommand is None:
        if print_mode:
            asyncio.run(_run_print_mode(ctx))
        else:
            asyncio.run(_run_interactive(ctx))


def _list_sessions(cwd: str) -> None:
    """Print a list of saved sessions."""
    from .session import list_sessions, _format_duration, _sessions_dir

    sessions = list_sessions()

    if not sessions:
        click.echo("No saved sessions found.")
        return

    sessions_path = _sessions_dir()
    click.echo(f"Sessions in {sessions_path}/:\n")
    for s in sessions:
        sid = s.get("session_id", "?")
        dur = _format_duration(s.get("ended_at", 0) - s.get("started_at", 0))
        title = s.get("title", "(no messages)")
        if len(title) > 50:
            title = title[:47] + "..."
        click.echo(f"  {sid}  {dur:>12s}  {title}")


def _parse_mcp_config(mcp_config: str | None) -> dict | None:
    """Parse --mcp-config argument: file path or inline JSON.

    Returns a dict of {server_name: config_dict} or None.
    """
    if not mcp_config:
        return None

    import json
    from pathlib import Path

    # Try as file path first
    path = Path(mcp_config)
    if path.is_file():
        data = json.loads(path.read_text())
        return data.get("mcpServers", data)

    # Try as inline JSON
    try:
        data = json.loads(mcp_config)
        return data.get("mcpServers", data)
    except json.JSONDecodeError:
        return None


def _save_session_on_exit() -> None:
    """Best-effort session save after Ink process exits.

    The primary save path is in IPCBridge when handling /exit (SHUTDOWN).
    This is a safety net for other exit cases (Ink crash, SIGTERM, etc.).
    """
    import time as _time
    from .state.store import Store
    from .session import save_session, find_session, extract_title, SessionMeta

    try:
        store = Store()
        messages = store.get("messages", [])
        if not messages:
            return
        # Skip if already saved by bridge (primary save path)
        session_id = store.get("session_id", "")
        if find_session(session_id):
            return
        title = store.get("title") or extract_title(messages)
        meta = SessionMeta(
            session_id=store.get("session_id", ""),
            title=title,
            started_at=store.get("session_start", _time.time()),
            model=store.get("model", ""),
            cwd=store.get("cwd", ""),
        )
        saved_path = save_session(
            store.get("session_id", ""),
            messages,
            meta,
        )
        if saved_path:
            # Print exit summary to stderr (stdout may be in raw mode after Ink)
            import sys
            from .session import format_exit_summary
            summary = format_exit_summary(
                session_id=store.get("session_id", ""),
                title=title,
                started_at=store.get("session_start", _time.time()),
                user_message_count=0,
                tool_call_count=0,
                total_messages=len(messages),
            )
            sys.stderr.write(summary + "\n")
    except Exception:
        pass  # Never let session save crash the shutdown path


async def _run_interactive(ctx: click.Context) -> None:
    """Launch interactive REPL session with Ink frontend."""
    import logging

    from .setup import setup
    from .ipc.fallback import check_ink_available
    from .ipc.transport import IPCTransport
    from .ipc.ink_launcher import InkLauncher
    from .ipc.bridge import IPCBridge
    from .tools.base import create_default_registry
    from .mcp.manager import MCPConnectionManager

    logger = logging.getLogger(__name__)

    setup(cwd=ctx.obj["cwd"], model=ctx.obj["model"],
          resume_from=ctx.obj.get("resume_from"), title=ctx.obj.get("title"))

    # Create shared tool registry (used by both MCP and QueryRunner)
    registry = create_default_registry()

    # Initialize MCP connections (non-blocking — tools register as servers come online)
    mcp_manager = MCPConnectionManager(registry)
    dynamic_configs = _parse_mcp_config(ctx.obj.get("mcp_config"))
    await mcp_manager.initialize(dynamic_configs=dynamic_configs)

    # Log MCP status
    status = mcp_manager.get_status()
    if status:
        for name, state in status.items():
            if state == "connected":
                logger.info("MCP: %s — connected", name)
            else:
                logger.warning("MCP: %s — %s", name, state)

    # Pre-flight check: Ink must be available
    available, reason = check_ink_available()
    if not available:
        click.echo(f"Error: Ink frontend unavailable: {reason}", err=True)
        await mcp_manager.shutdown()
        raise SystemExit(1)

    transport = IPCTransport()
    launcher = InkLauncher()
    bridge = IPCBridge(
        transport=transport,
        model=ctx.obj["model"],
        allowed_tools=ctx.obj.get("allowed_tools"),
        disallowed_tools=ctx.obj.get("disallowed_tools"),
        permission_mode=ctx.obj.get("permission_mode"),
        tool_registry=registry,
    )

    try:
        # 1. Launch Ink process (creates pipes, spawns node)
        success = await launcher.launch()
        if not success:
            click.echo("Error: Ink frontend failed to start.", err=True)
            raise SystemExit(1)

        # 2. Start IPC transport over the pipes
        await transport.start(
            process=launcher.process,
            tx_fd=launcher.tx_fd,
            rx_fd=launcher.rx_fd,
        )

        # 3. Wait briefly for Node.js to start up, then send session ready
        await asyncio.sleep(0.2)
        await bridge.send_session_ready()
        # Skip welcome screen when resuming a previous session;
        # show a summary of the previous conversation instead
        from .state.store import Store
        if Store().get("resumed", False):
            await bridge.send_resume_info()
        else:
            await bridge.send_welcome()

        # 5. Wait for Ink process to exit
        # Ink owns the terminal, user interacts directly with it.
        # When the user exits Ink (e.g., /exit or Ctrl+C), the process ends.
        while launcher.is_alive:
            await asyncio.sleep(0.5)

        logger.info("Ink: session ended")

    except SystemExit:
        raise
    except Exception:
        logger.exception("Ink: unexpected error")
        raise SystemExit(1)
    finally:
        # Save session on all exit paths (normal, Ctrl+C, crash)
        _save_session_on_exit()
        await launcher.shutdown()
        await transport.close()
        await mcp_manager.shutdown()


async def _run_print_mode(ctx: click.Context) -> None:
    """Non-interactive: read prompt from stdin, print response, exit."""
    import sys

    from .setup import setup
    from .query import QueryRunner
    from .tools.base import create_default_registry
    from .mcp.manager import MCPConnectionManager

    prompt = sys.stdin.read().strip()
    if not prompt:
        return

    setup(cwd=ctx.obj["cwd"], model=ctx.obj["model"])

    # Create shared tool registry with MCP tools
    registry = create_default_registry()
    mcp_manager = MCPConnectionManager(registry)
    dynamic_configs = _parse_mcp_config(ctx.obj.get("mcp_config"))
    await mcp_manager.initialize(dynamic_configs=dynamic_configs)

    try:
        runner = QueryRunner(model=ctx.obj["model"], tool_registry=registry, enable_streaming_tools=True)
        async for event in runner.run(prompt):
            if event.type == "text":
                click.echo(event.content, nl=False)
        click.echo()
    finally:
        await mcp_manager.shutdown()


@cli.command()
def auth() -> None:
    """Configure API key, base URL, and model."""
    from .config.auth import configure_interactive

    configure_interactive()


@cli.command()
def config() -> None:
    """Show current configuration."""
    from .config.settings import show_config

    show_config()


if __name__ == "__main__":
    cli()
