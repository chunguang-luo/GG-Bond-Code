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
@click.option("--allowed-tools", "allowed_tools", default=None, help="Comma-separated list of allowed tools.")
@click.option("--disallowed-tools", "disallowed_tools", default=None, help="Comma-separated list of disallowed tools.")
@click.option("--mcp-config", "mcp_config", default=None,
              help="Path to MCP server config JSON, or inline JSON.")
@click.option("--permission-mode", "permission_mode", default=None,
              type=click.Choice(["default", "plan", "acceptEdits", "bypassPermissions", "dontAsk"]),
              help="Permission mode for this session.")
@click.pass_context
def cli(ctx: click.Context, version: bool, print_mode: bool, model: str | None,
        cwd: str | None, allowed_tools: str | None, disallowed_tools: str | None,
        mcp_config: str | None, permission_mode: str | None) -> None:
    """NextCode — AI-powered CLI assistant."""
    ctx.ensure_object(dict)

    if version:
        from . import __version__
        click.echo(f"{__version__} (NextCode)")
        return

    # Run init (memoized — only executes once)
    init()

    # Store options in context
    ctx.obj["model"] = model
    ctx.obj["cwd"] = cwd or str(Path.cwd())
    ctx.obj["print_mode"] = print_mode
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

    setup(cwd=ctx.obj["cwd"], model=ctx.obj["model"])

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
