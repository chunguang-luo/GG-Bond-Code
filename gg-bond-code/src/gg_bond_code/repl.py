"""Interactive REPL — mirrors replLauncher.tsx + REPL component."""

from __future__ import annotations

import asyncio
import time

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from .query import QueryRunner, QueryEvent
from .state.store import Store
from .state.context import create_store_context
from .permissions.manager import PermissionDecision


class REPL:
    """Interactive read-eval-print loop."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model
        self.console = Console()
        self._context = create_store_context()
        self.runner = QueryRunner(model=model, permission_callback=self._ask_permission, context=self._context)
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
            # Green text for user input (ANSI escape inherited from prompt)
            user_input = input("\x1b[32mggbond> \x1b[32m").strip()
            # Reset color so subsequent output is not green
            self.console.file.write("\x1b[0m")
            if user_input:
                # Clear the line and reprint only the input text in green
                self.console.file.flush()
            return user_input
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
        """Run a query and display results.

        Strategy: stream raw text via print for instant feedback,
        then re-render the full response as Markdown once complete.
        """
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_start_times: dict[str, float] = {}  # tool_use_id → start time
        in_thinking = False  # track if we're inside a thinking block
        output_line_count = 0  # track total output lines for cursor positioning
        gen = None  # track the async generator for cleanup
        first_text = True  # track if this is the first text event
        seen_tool = False  # track if we've seen any tool event

        try:
            gen = self.runner.run(user_input)
            async for event in gen:
                if event.type == "text":
                    if in_thinking and self.show_thinking:
                        self.console.print("\n< /Thinking >\n", style="dim", end="")
                        output_line_count += 2  # newline + tag line
                        in_thinking = False
                    text_parts.append(event.content)
                    # Stream raw text for instant feedback
                    if seen_tool or first_text:
                        # After tools or first text: start on a new line
                        self.console.print()
                        output_line_count += 1
                        first_text = False
                        seen_tool = False
                    content_lines = event.content.split("\n")
                    output_line_count += len(content_lines) - 1
                    self.console.print(event.content, end="", highlight=False)
                    self.console.file.flush()

                elif event.type == "thinking":
                    thinking_parts.append(event.content)
                    if self.show_thinking:
                        if not in_thinking:
                            self.console.print("< Thinking > ", style="dim", end="")
                            output_line_count += 1  # tag line
                            in_thinking = True
                        content_lines = event.content.split("\n")
                        output_line_count += len(content_lines) - 1
                        self.console.print(event.content, end="", style="dim italic")
                        self.console.file.flush()

                elif event.type == "tool_start":
                    seen_tool = True
                    if event.tool_use_id:
                        tool_start_times[event.tool_use_id] = time.monotonic()
                    self.console.print(
                        f"\n  ⚙ {event.tool_name}...",
                        style="dim cyan",
                    )
                    output_line_count += 1  # tool line (print adds newline, \n just moves cursor)

                elif event.type == "tool_use":
                    seen_tool = True
                    if event.tool_use_id and event.tool_use_id not in tool_start_times:
                        tool_start_times[event.tool_use_id] = time.monotonic()
                    self.console.print()
                    output_line_count += 1
                    if event.tool_purpose:
                        self.console.print(f"  {event.tool_purpose}", style="dim")
                        output_line_count += 1
                    self.console.print(
                        f"  ⚙ {event.tool_name}({self._format_params(event.tool_input)})",
                        style="dim cyan",
                    )
                    output_line_count += 1

                elif event.type == "tool_result":
                    seen_tool = True
                    elapsed = ""
                    if event.tool_use_id and event.tool_use_id in tool_start_times:
                        t0 = tool_start_times.pop(event.tool_use_id)
                        elapsed = f" ({time.monotonic() - t0:.1f}s)"

                    if event.tool_error:
                        self.console.print(
                            f"  ✗ {event.tool_name}{elapsed}",
                            style="red",
                        )
                        output_line_count += 1
                        error_lines = event.tool_result.split("\n")
                        self.console.print(f"    {event.tool_result}", style="dim red")
                        output_line_count += len(error_lines)
                    else:
                        result = event.tool_result
                        if len(result) > 500:
                            result = result[:500] + "..."
                        self.console.print(f"  ✓ {event.tool_name}{elapsed}", style="green")
                        output_line_count += 1
                        if self.show_tool_details and result.strip():
                            result_lines = result.split("\n")
                            self.console.print(f"    {result}", style="dim")
                            output_line_count += len(result_lines)

                elif event.type == "error":
                    seen_tool = True
                    self.console.print(Panel(event.content, title="Error", border_style="red"))
                    # Estimate Panel lines (title + content + border)
                    # This is approximate; Panel rendering is complex
                    error_lines = event.content.count("\n") + 4  # border, title, content, border
                    output_line_count += error_lines

        except asyncio.CancelledError:
            if gen is not None:
                await gen.aclose()
            self.console.print("\n[query stopped]", style="yellow")
            return

        # Close thinking block if still open
        if in_thinking and self.show_thinking:
            self.console.print("\n< /Thinking >\n", style="dim")
            output_line_count += 2  # newline + tag line

        # Re-render the full text response as formatted Markdown
        full_text = "".join(text_parts).strip()
        if full_text:
            # Move cursor up to overwrite all output
            if output_line_count > 0:
                self.console.file.write(f"\x1b[{output_line_count}A")  # cursor up
            self.console.file.write("\x1b[J")  # clear from cursor down
            self.console.file.flush()
            self.console.print(Markdown(full_text))
            self.console.print()

    def _handle_command(self, command: str) -> bool:
        """Handle slash commands. Returns True if should continue REPL."""
        cmd = command.strip().lower()
        store = Store()

        if cmd in ("/exit", "/quit", "/q"):
            self.running = False
            return False
        elif cmd == "/clear":
            store.set("messages", [])
            self._context = create_store_context()
            self.runner = QueryRunner(model=self.model, permission_callback=self._ask_permission, context=self._context)
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
