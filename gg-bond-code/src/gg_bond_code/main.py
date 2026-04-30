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
@click.pass_context
def cli(ctx: click.Context, version: bool, print_mode: bool, model: str | None, cwd: str | None) -> None:
    """GG Bond Code — AI-powered CLI assistant."""
    ctx.ensure_object(dict)

    if version:
        from . import __version__
        click.echo(f"{__version__} (GG Bond Code)")
        return

    # Run init (memoized — only executes once)
    init()

    # Store options in context
    ctx.obj["model"] = model
    ctx.obj["cwd"] = cwd or str(Path.cwd())
    ctx.obj["print_mode"] = print_mode

    # If no sub-command, launch interactive REPL
    if ctx.invoked_subcommand is None:
        if print_mode:
            asyncio.run(_run_print_mode(ctx))
        else:
            asyncio.run(_run_interactive(ctx))


async def _run_interactive(ctx: click.Context) -> None:
    """Launch interactive REPL session."""
    from .setup import setup
    from .repl import REPL

    setup(cwd=ctx.obj["cwd"], model=ctx.obj["model"])
    repl = REPL(model=ctx.obj["model"])
    await repl.run()


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
