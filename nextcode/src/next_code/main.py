"""Main orchestrator — Click command definitions + init dispatch."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from .init import init


@click.group(invoke_without_command=True)
@click.option("--version", "-v", is_flag=True, help="Show version.")
@click.option("--print", "print_mode", is_flag=True, help="Non-interactive mode (read from stdin).")
@click.option("--model", default=None, help="Model to use.")
@click.option("--cwd", default=None, help="Working directory.")
@click.option("--ink", "ink_mode", default=None,
              type=click.Choice(["off", "auto", "on"], case_sensitive=False),
              help="Ink frontend mode: off (Rich REPL), auto (try Ink, fallback), on (require Ink).")
@click.pass_context
def cli(ctx: click.Context, version: bool, print_mode: bool, model: str | None,
        cwd: str | None, ink_mode: str | None) -> None:
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
    ctx.obj["ink_mode"] = ink_mode

    # If no sub-command, launch interactive REPL
    if ctx.invoked_subcommand is None:
        if print_mode:
            asyncio.run(_run_print_mode(ctx))
        else:
            asyncio.run(_run_interactive(ctx))


async def _run_interactive(ctx: click.Context) -> None:
    """Launch interactive REPL session."""
    from .setup import setup
    from .ipc.fallback import resolve_ink_mode, InkMode, check_ink_available

    setup(cwd=ctx.obj["cwd"], model=ctx.obj["model"])

    mode = resolve_ink_mode(ctx.obj.get("ink_mode"))

    if mode == InkMode.OFF:
        # Always use Rich REPL
        await _run_rich_repl(ctx)
    elif mode == InkMode.ON:
        # Require Ink, fail if unavailable
        available, reason = check_ink_available()
        if not available:
            click.echo(f"Error: Ink frontend unavailable: {reason}", err=True)
            raise SystemExit(1)
        success = await _run_with_ink(ctx)
        if not success:
            click.echo("Error: Ink frontend failed to start.", err=True)
            raise SystemExit(1)
    else:
        # AUTO: try Ink, fall back to Rich
        available, reason = check_ink_available()
        if available:
            success = await _run_with_ink(ctx)
            if not success:
                click.echo(f"Ink frontend failed, falling back to Rich REPL...", err=True)
                await _run_rich_repl(ctx)
        else:
            click.echo(f"Ink unavailable ({reason}), using Rich REPL", err=True)
            await _run_rich_repl(ctx)


async def _run_rich_repl(ctx: click.Context) -> None:
    """Launch the Rich-based REPL (original path)."""
    from .repl import REPL

    repl = REPL(model=ctx.obj["model"])
    await repl.run()


async def _run_with_ink(ctx: click.Context) -> bool:
    """Launch with Ink frontend. Returns True if successful."""
    import logging

    from .ipc.transport import IPCTransport
    from .ipc.ink_launcher import InkLauncher
    from .ipc.bridge import IPCBridge

    logger = logging.getLogger(__name__)

    transport = IPCTransport()
    launcher = InkLauncher(socket_path=transport.socket_path)
    bridge = IPCBridge(transport=transport, model=ctx.obj["model"])

    try:
        # 1. Start IPC transport
        await transport.start()

        # 2. Launch Ink process (Ink inherits terminal stdin/stdout/stderr)
        success = await launcher.launch()
        if not success:
            logger.warning("Ink: launch failed")
            return False

        # 3. Wait for Ink to connect via IPC socket
        connected = await transport.wait_for_connection(timeout=10.0)
        if not connected:
            logger.warning("Ink: no connection from frontend")
            await launcher.shutdown()
            return False

        # 4. Send session ready + welcome
        await bridge.send_session_ready()
        await bridge.send_welcome()

        # 5. Wait for Ink process to exit
        # Ink owns the terminal, user interacts directly with it.
        # When the user exits Ink (e.g., /exit or Ctrl+C), the process ends.
        while launcher.is_alive:
            await asyncio.sleep(0.5)

        logger.info("Ink: session ended")
        return True

    except Exception:
        logger.exception("Ink: unexpected error")
        return False
    finally:
        await launcher.shutdown()
        await transport.close()


async def _run_print_mode(ctx: click.Context) -> None:
    """Non-interactive: read prompt from stdin, print response, exit."""
    import sys

    from .setup import setup
    from .query import QueryRunner

    prompt = sys.stdin.read().strip()
    if not prompt:
        return

    setup(cwd=ctx.obj["cwd"], model=ctx.obj["model"])
    runner = QueryRunner(model=ctx.obj["model"], enable_streaming_tools=True)
    async for event in runner.run(prompt):
        if event.type == "text":
            click.echo(event.content, nl=False)
    click.echo()


@cli.command()
def auth() -> None:
    """Configure API key."""
    from .config.auth import configure_api_key

    configure_api_key()


@cli.command()
def config() -> None:
    """Show current configuration."""
    from .config.settings import show_config

    show_config()


if __name__ == "__main__":
    cli()
