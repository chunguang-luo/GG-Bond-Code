"""Interactive REPL — mirrors replLauncher.tsx + REPL component."""

from __future__ import annotations

import asyncio
import time

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel

from .context.system import clear_system_context_cache
from .query import QueryRunner, QueryEvent
from .state.store import Store
from .state.context import create_store_context
from .permissions.manager import PermissionDecision
from .prefetch import start_deferred_prefetches


class REPL:
    """Interactive read-eval-print loop."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model
        self.console = Console()
        self._context = create_store_context()
        self.runner = QueryRunner(model=model, permission_callback=self._ask_permission, context=self._context, enable_streaming_tools=True)
        self.running = True
        self._last_interrupt_time: float = 0.0  # for double-Ctrl+C exit
        self._current_task: asyncio.Task | None = None  # track running query task
        self._live: Live | None = None  # reference to current Live instance for permission prompts

    # ── UI preference accessors (backed by Store) ─────────────────────

    @property
    def show_thinking(self) -> bool:
        return Store().get("ui.show_thinking", False)

    @show_thinking.setter
    def show_thinking(self, value: bool) -> None:
        Store().set("ui.show_thinking", value)

    @property
    def show_tool_details(self) -> bool:
        return Store().get("ui.show_tool_details", True)

    @show_tool_details.setter
    def show_tool_details(self, value: bool) -> None:
        Store().set("ui.show_tool_details", value)

    async def run(self) -> None:
        """Main REPL loop."""
        self._print_welcome()

        # Start prefetch in background
        await start_deferred_prefetches()

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
        """Async permission callback — pauses Live display before asking."""
        # Stop Live display to allow user input
        if self._live is not None:
            self._live.stop()

        try:
            # Get user permission
            decision = await asyncio.to_thread(self.runner.permissions.ask_user, tool_name, params)
        finally:
            # Resume Live display after getting user input
            if self._live is not None:
                self._live.start()

        return decision

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

        Strategy: Use Live to render Markdown output in real-time,
        accumulating content and updating the display efficiently.
        """
        thinking_parts: list[str] = []
        tool_start_times: dict[str, float] = {}  # tool_use_id → start time
        in_thinking = False  # track if we're inside a thinking block
        gen = None  # track the async generator for cleanup

        # Build the content to display in Live
        text_output: list[str] = []

        # Create Live with default overflow handling to avoid duplication
        # transient=False keeps content after Live exits
        try:
            self._live = Live(Markdown(""), console=self.console, refresh_per_second=4, vertical_overflow="visible")
            self._live.start()

            gen = self.runner.run(user_input)
            async for event in gen:
                if event.type == "text":
                    if in_thinking and self.show_thinking:
                        text_output.append("```\n")
                        in_thinking = False
                    text_output.append(event.content)

                elif event.type == "thinking":
                    thinking_parts.append(event.content)
                    if self.show_thinking:
                        if not in_thinking:
                            text_output.append("\n\n---\n**🤔 Thinking**\n```text\n")
                            in_thinking = True
                        text_output.append(event.content)

                elif event.type == "tool_start":
                    # Stop Live display before permission check and tool execution
                    if self._live is not None:
                        self._live.stop()

                    if event.tool_use_id:
                        tool_start_times[event.tool_use_id] = time.monotonic()

                elif event.type == "tool_use":
                    if event.tool_use_id and event.tool_use_id not in tool_start_times:
                        tool_start_times[event.tool_use_id] = time.monotonic()
                    if event.tool_purpose:
                        text_output.append(f"\n\n{event.tool_purpose}")
                    params = self._format_params(event.tool_input)
                    text_output.append(f"\n\n⚙️ `{event.tool_name}` ({params})")

                    # Resume Live display after permission is handled
                    if self._live is not None:
                        self._live.start()

                elif event.type == "tool_result":
                    elapsed = ""
                    if event.tool_use_id and event.tool_use_id in tool_start_times:
                        t0 = tool_start_times.pop(event.tool_use_id)
                        elapsed_ms = (time.monotonic() - t0) * 1000
                        elapsed = f" ({elapsed_ms:.0f}ms)"

                    if event.tool_error:
                        text_output.append(f"\n\n❌ `{event.tool_name}`{elapsed}\n\n")
                        if self.show_tool_details:
                            text_output.append(f"\n\n📄 结果内容 :\n\n```python\n{event.tool_result}\n```\n\n")
                    else:
                        result = event.tool_result
                        if len(result) > 500:
                            result = result[:500] + "..."
                        if self.show_tool_details and result.strip():
                            text_output.append(f"\n\n✅ `{event.tool_name}`{elapsed}\n\n")
                            text_output.append(f"\n\n📄 结果内容 :\n\n```python\n{result}\n```\n\n")
                        else:
                            text_output.append(f"\n\n✅ `{event.tool_name}`{elapsed}\n\n")

                elif event.type == "error":
                    text_output.append(f"\n\n**Error:** {event.content}")

                # Update Live display with current accumulated content
                # Let Live handle refresh timing automatically
                full_text = "".join(text_output)
                if full_text.strip() and self._live is not None:
                    self._live.update(Markdown(full_text))

        except asyncio.CancelledError:
            if gen is not None:
                await gen.aclose()
            self.console.print("\n[query stopped]", style="yellow")
            return
        finally:
            # Ensure Live is stopped and cleaned up
            if self._live is not None:
                self._live.stop()

            # Close thinking block if still open
            if in_thinking and self.show_thinking:
                text_output.append("```\n")

            # Final render with all content as Markdown
            full_text = "".join(text_output).strip()
            if full_text:
                self.console.print(Markdown(full_text))

    def _handle_command(self, command: str) -> bool:
        """Handle slash commands. Returns True if should continue REPL."""
        cmd = command.strip().lower()
        store = Store()

        if cmd in ("/exit", "/quit", "/q"):
            self.running = False
            return False
        elif cmd == "/clear":
            clear_system_context_cache()  # Clear system context cache
            store.set("messages", [])
            self._context = create_store_context()
            self.runner = QueryRunner(model=self.model, permission_callback=self._ask_permission, context=self._context, enable_streaming_tools=True)
            self.console.clear()
            self._print_welcome()
            return False
        elif cmd == "/thinking":
            self.show_thinking = not self.show_thinking
            state = "ON" if self.show_thinking else "OFF"
            self.console.print(f"Thinking display: [green]{state}[/green]")
            return False
        elif cmd == "/verbose":
            self.show_tool_details = not self.show_tool_details
            state = "ON" if self.show_tool_details else "OFF"
            self.console.print(f"Tool details: [green]{state}[/green]")
            return False
        elif cmd == "/help":
            self._print_help()
            return False
        elif cmd == "/compact":
            clear_system_context_cache()  # Clear system context cache
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
        elif cmd == "/log":
            self._print_transition_log()
            return False
        else:
            self.console.print(f"Unknown command: {cmd}", style="yellow")
            # Suggest similar commands
            similar = self._find_similar_command(cmd)
            if similar:
                self.console.print(f"Did you mean: [green]{similar}[/green]?")
            return False

    def _find_similar_command(self, cmd: str) -> str | None:
        """Find similar command suggestion using simple string similarity."""
        valid_commands = ["/help", "/clear", "/compact", "/thinking", "/verbose", "/model", "/log", "/exit", "/quit", "/q"]
        best_match = None
        best_score = 0

        for valid_cmd in valid_commands:
            # Simple similarity: count matching characters at start
            score = 0
            min_len = min(len(cmd), len(valid_cmd))
            for i in range(min_len):
                if cmd[i] == valid_cmd[i]:
                    score += 1
                else:
                    break

            # Bonus for same length
            if len(cmd) == len(valid_cmd):
                score += 1

            if score > best_score and score >= 2:  # At least 2 matching chars
                best_score = score
                best_match = valid_cmd

        return best_match

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

    def _print_transition_log(self) -> None:
        """Print the transition log from the last query run."""
        state = self.runner.loop_state
        log = state.get_log()
        if not log:
            self.console.print("[dim]No transition log available. Run a query first.[/dim]")
            return

        self.console.print(
            Panel(
                state.format_log(),
                title=f"Transition Log ({state.transition_count} transitions, {state.turn_count} turns)",
                border_style="cyan",
            )
        )

    def _print_help(self) -> None:
        self.console.print(
            Panel(
                "/help     - Show this help\n"
                "/clear    - Clear conversation\n"
                "/compact  - Compact conversation history\n"
                "/thinking - Toggle thinking display\n"
                "/verbose  - Toggle tool result details\n"
                "/model    - Show current model\n"
                "/log      - Show last query transition log\n"
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
