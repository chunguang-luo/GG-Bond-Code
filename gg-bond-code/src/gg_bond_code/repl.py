"""Interactive REPL — mirrors replLauncher.tsx + REPL component."""

from __future__ import annotations

import asyncio
import time

from rich.console import Console
from rich.panel import Panel

from .query import QueryRunner, QueryEvent
from .state.store import Store
from .permissions.manager import PermissionDecision


class REPL:
    """Interactive read-eval-print loop."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model
        self.console = Console()
        self.runner = QueryRunner(model=model, permission_callback=self._ask_permission)
        self.running = True
        self._last_interrupt_time: float = 0.0  # for double-Ctrl+C exit
        self._current_task: asyncio.Task | None = None  # track running query task

    # ── UI preference accessors (backed by Store) ─────────────────────

    @property
    def show_thinking(self) -> bool:
        return Store().get("ui.show_thinking", False)

    @show_thinking.setter
    def show_thinking(self, value: bool) -> None:
        Store().set("ui.show_thinking", value)

    @property
    def show_tool_details(self) -> bool:
        return Store().get("ui.show_tool_details", False)

    @show_tool_details.setter
    def show_tool_details(self, value: bool) -> None:
        Store().set("ui.show_tool_details", value)

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

                # Run query as a task so we can cancel it
                self._current_task = asyncio.create_task(self._run_query(user_input))
                try:
                    await self._current_task
                except KeyboardInterrupt:
                    self._handle_interrupt()
                finally:
                    self._current_task = None

            except KeyboardInterrupt:
                self._handle_interrupt()
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

    async def _ask_permission(self, tool_name: str, params: dict) -> PermissionDecision:
        """Async permission callback — wraps sync ask_user via to_thread."""
        return await asyncio.to_thread(self.runner.permissions.ask_user, tool_name, params)

    def _handle_interrupt(self) -> None:
        """Handle Ctrl+C: first press cancels current query, second press exits REPL."""
        now = time.monotonic()
        if self._current_task is not None and not self._current_task.done():
            # Cancel the running query task
            self._current_task.cancel()
            self.console.print("\n[interrupted — query cancelled]", style="yellow")
        elif now - self._last_interrupt_time < 3.0:
            # Double Ctrl+C within 3 seconds → exit
            self.console.print("\n[exiting...]", style="yellow")
            self.running = False
        else:
            self.console.print("\n[interrupted — press Ctrl+C again to exit]", style="yellow")
        self._last_interrupt_time = now

    async def _run_query(self, user_input: str) -> None:
        """Run a query and display results."""
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_start_times: dict[str, float] = {}  # tool_use_id → start time
        in_thinking = False  # track if we're inside a thinking block
        gen = None  # track the async generator for cleanup

        try:
            gen = self.runner.run(user_input)
            async for event in gen:
                if event.type == "text":
                    # If we were showing thinking, close it before text output
                    if in_thinking and self.show_thinking:
                        self.console.print("\n< /Thinking >\n", style="dim", end="")
                        in_thinking = False
                    # Stream text directly
                    self.console.print(event.content, end="", highlight=False)
                    self.console.file.flush()
                    text_parts.append(event.content)

                elif event.type == "thinking":
                    thinking_parts.append(event.content)
                    if self.show_thinking:
                        if not in_thinking:
                            self.console.print("< Thinking > ", style="dim", end="")
                            in_thinking = True
                        self.console.print(event.content, end="", style="dim italic")
                        self.console.file.flush()

                elif event.type == "tool_start":
                    tool_id = event.tool_input.get("id", "")
                    tool_start_times[tool_id] = time.monotonic()
                    self.console.print(
                        f"  ⚙ {event.tool_name}...",
                        style="dim cyan",
                    )

                elif event.type == "tool_use":
                    self.console.print()
                    self.console.print(
                        f"  ⚙ {event.tool_name}({self._format_params(event.tool_input)})",
                        style="dim cyan",
                    )

                elif event.type == "tool_result":
                    elapsed = ""
                    # Try to find matching start time (approximate match by tool_name)
                    for tid, t0 in list(tool_start_times.items()):
                        elapsed = f" ({time.monotonic() - t0:.1f}s)"
                        del tool_start_times[tid]
                        break

                    if event.tool_error:
                        self.console.print(
                            f"  ✗ {event.tool_name}{elapsed}: {event.tool_result[:200]}",
                            style="red",
                        )
                    else:
                        result = event.tool_result
                        if len(result) > 500:
                            result = result[:500] + "..."
                        self.console.print(f"  ✓ {event.tool_name}{elapsed}", style="green")
                        if self.show_tool_details and result.strip():
                            self.console.print(f"    {result}", style="dim")

                elif event.type == "error":
                    self.console.print(f"\nError: {event.content}", style="bold red")

        except asyncio.CancelledError:
            # Query was cancelled by Ctrl+C — clean up the generator
            if gen is not None:
                await gen.aclose()
            self.console.print("\n[query stopped]", style="yellow")
            return

        # Close thinking block if still open
        if in_thinking and self.show_thinking:
            self.console.print("\n< /Thinking >\n", style="dim")

        if text_parts:
            self.console.print()  # newline after response

    def _handle_command(self, command: str) -> bool:
        """Handle slash commands. Returns True if should continue REPL."""
        cmd = command.strip().lower()
        store = Store()

        if cmd in ("/exit", "/quit", "/q"):
            self.running = False
            return False
        elif cmd == "/clear":
            store.set("messages", [])
            self.runner = QueryRunner(model=self.model, permission_callback=self._ask_permission)
            self.console.clear()
            self._print_welcome()
            return False
        elif cmd == "/thinking":
            self.show_thinking = not self.show_thinking
            state = "ON" if self.show_thinking else "OFF"
            self.console.print(f"Thinking display: [green]{state}[/green]")
            return False
        elif cmd == "/show-tool-details":
            self.show_tool_details = not self.show_tool_details
            state = "ON" if self.show_tool_details else "OFF"
            self.console.print(f"Tool details: [green]{state}[/green]")
            return False
        elif cmd == "/help":
            self._print_help()
            return False
        elif cmd == "/compact":
            self.console.print("[dim]Compacting conversation...[/dim]")
            messages = store.get("messages", [])
            if messages:
                # Simple compact: keep only last 4 messages
                compacted = messages[-4:]
                store.set("messages", compacted)
                self.console.print(f"[green]Compacted {len(messages)} → {len(compacted)} messages[/green]")
            return False
        elif cmd == "/model":
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
                "/help     - Show this help\n"
                "/clear    - Clear conversation\n"
                "/compact  - Compact conversation history\n"
                "/thinking - Toggle thinking display\n"
                "/show-tool-details - Toggle tool result details\n"
                "/model    - Show current model\n"
                "/exit     - Exit the REPL",
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
