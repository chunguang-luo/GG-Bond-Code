"""Interactive REPL — mirrors replLauncher.tsx + REPL component."""

from __future__ import annotations

import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from gg_bond_code.query import QueryRunner, QueryEvent
from gg_bond_code.state.store import Store
from gg_bond_code.permissions.manager import PermissionManager, PermissionDecision


class REPL:
    """Interactive read-eval-print loop."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model
        self.console = Console()
        self.runner = QueryRunner(model=model)
        self.running = True

    async def run(self) -> None:
        """Main REPL loop."""
        self._print_welcome()

        while self.running:
            try:
                # Read input
                user_input = self._read_input()
                if user_input is None:
                    continue

                # Handle slash commands
                if user_input.startswith("/"):
                    should_continue = self._handle_command(user_input)
                    if not should_continue:
                        continue
                    continue

                if not user_input.strip():
                    continue

                # Run query
                await self._run_query(user_input)

            except KeyboardInterrupt:
                self.console.print("\n[interrupted]", style="yellow")
                continue
            except EOFError:
                break

        self._print_goodbye()

    def _read_input(self) -> str | None:
        """Read user input with a prompt."""
        try:
            return input("ggbond> ").strip()
        except EOFError:
            self.running = False
            return None

    async def _run_query(self, user_input: str) -> None:
        """Run a query and display results."""
        text_parts: list[str] = []

        async for event in self.runner.run(user_input):
            if event.type == "text":
                # Stream text directly
                self.console.print(event.content, end="", highlight=False)
                text_parts.append(event.content)
            elif event.type == "tool_use":
                self.console.print()
                self.console.print(
                    f"  ⚙ {event.tool_name}({self._format_params(event.tool_input)})",
                    style="dim cyan",
                )
            elif event.type == "tool_result":
                if event.tool_error:
                    self.console.print(
                        f"  ✗ {event.tool_name}: {event.tool_result[:200]}",
                        style="red",
                    )
                else:
                    # Show truncated result
                    result = event.tool_result
                    if len(result) > 500:
                        result = result[:500] + "..."
                    self.console.print(f"  ✓ {event.tool_name}", style="green")
            elif event.type == "error":
                self.console.print(f"\nError: {event.content}", style="bold red")
            elif event.type == "thinking":
                # Optionally show thinking
                pass

        if text_parts:
            self.console.print()  # newline after response

    def _handle_command(self, command: str) -> bool:
        """Handle slash commands. Returns True if should continue REPL."""
        cmd = command.strip().lower()

        if cmd in ("/exit", "/quit", "/q"):
            self.running = False
            return False
        elif cmd == "/clear":
            store = Store()
            store.set("messages", [])
            self.runner = QueryRunner(model=self.model)
            self.console.clear()
            self._print_welcome()
            return False
        elif cmd == "/help":
            self._print_help()
            return False
        elif cmd == "/compact":
            self.console.print("[dim]Compacting conversation...[/dim]")
            store = Store()
            messages = store.get("messages", [])
            if messages:
                # Simple compact: keep only last 4 messages
                compacted = messages[-4:]
                store.set("messages", compacted)
                self.console.print(f"[green]Compacted {len(messages)} → {len(compacted)} messages[/green]")
            return False
        elif cmd == "/model":
            store = Store()
            self.console.print(f"Current model: {store.get('model', 'unknown')}")
            return False
        else:
            self.console.print(f"Unknown command: {command}", style="yellow")
            return False

    def _print_welcome(self) -> None:
        store = Store()
        self.console.print(
            Panel(
                f"GG Bond Code v0.1.0\nModel: {store.get('model', 'unknown')}\nCWD: {store.get('cwd', 'unknown')}",
                title="GG Bond Code",
                border_style="blue",
            )
        )
        self.console.print("Type /help for commands, /exit to quit.\n")

    def _print_goodbye(self) -> None:
        self.console.print("\nGoodbye!", style="blue")

    def _print_help(self) -> None:
        self.console.print(
            Panel(
                "/help    - Show this help\n"
                "/clear   - Clear conversation\n"
                "/compact - Compact conversation history\n"
                "/model   - Show current model\n"
                "/exit    - Exit the REPL",
                title="Commands",
                border_style="green",
            )
        )

    @staticmethod
    def _format_params(params: dict) -> str:
        """Format tool params for display."""
        parts = []
        for k, v in params.items():
            val = str(v)
            if len(val) > 60:
                val = val[:60] + "..."
            parts.append(f"{k}={val!r}")
        return ", ".join(parts)
