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
                    should_continue = await self._handle_command(user_input)
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
                    # Format: ⚙️ ToolName(key_param=value, ...)
                    tool_label = self._format_tool_label(event.tool_name, event.tool_input)
                    text_output.append(f"\n\n⚙️ {tool_label}")

                    # Resume Live display after permission is handled
                    if self._live is not None:
                        self._live.start()

                elif event.type == "tool_result":
                    elapsed = ""
                    if event.tool_use_id and event.tool_use_id in tool_start_times:
                        t0 = tool_start_times.pop(event.tool_use_id)
                        elapsed_ms = (time.monotonic() - t0) * 1000
                        elapsed = f" ({elapsed_ms:.0f}ms)"

                    # Format: ⎿ result summary
                    if event.tool_error:
                        text_output.append(f"\n\n  ⎿  Error{elapsed}\n\n")
                        if event.tool_result.strip():
                            text_output.append("```\n")
                            for line in event.tool_result.splitlines()[:10]:
                                text_output.append(f"  {line}\n")
                            text_output.append("```\n")
                    else:
                        result_summary = self._format_tool_result(event.tool_name, event.tool_result)
                        text_output.append(f"\n\n  ⎿  {result_summary}{elapsed}\n\n")


                elif event.type == "error":
                    text_output.append(f"\n\n**Error:** {event.content}")

                elif event.type == "warning":
                    level = event.metadata.get("level", "warning")
                    percent = event.metadata.get("percent_used", 0)
                    bar_len = 20
                    filled = min(bar_len, round(bar_len * percent / 100))
                    bar = "█" * filled + "░" * (bar_len - filled)
                    if level == "blocking":
                        style = "bold red"
                    elif level == "error":
                        style = "red"
                    else:
                        style = "yellow"
                    text_output.append(
                        f"\n\n[{style}]Context: [{bar}] {percent}% — {event.content}[/{style}]"
                    )

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
            # Close thinking block if still open
            if in_thinking and self.show_thinking:
                text_output.append("```\n")

            # Ensure Live is stopped and cleaned up
            if self._live is not None:
                self._live.stop()

            # Live with transient=False keeps content after exit, so no need to reprint

    async def _handle_command(self, command: str) -> bool:
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
        elif cmd == "/help":
            self._print_help()
            return False
        elif cmd == "/compact":
            clear_system_context_cache()  # Clear system context cache
            self.console.print("[dim]Compacting conversation...[/dim]")
            messages = store.get("messages", [])
            if messages:
                from .compact.manager import CompactManager, CompactLevel
                manager = CompactManager(model=self.model or store.get("model", "deepseek-chat"))
                # Force FULL level for manual /compact
                compacted, reason = await manager.execute(CompactLevel.FULL, messages)
                store.set("messages", compacted)
                self.console.print(f"[green]Compacted: {reason}[/green]")
            return False
        elif cmd == "/model":
            self.console.print(f"Current model: {store.get('model', 'unknown')}")
            return False
        elif cmd == "/context":
            self._print_context_info(store)
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
        valid_commands = ["/help", "/clear", "/compact", "/context", "/thinking", "/model", "/log", "/exit", "/quit", "/q"]
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
        model = self.model or store.get("model", "unknown")
        cwd = store.get("cwd", "unknown")
        # Shorten cwd: show last 2 segments or ~-relative path
        import os
        home = os.path.expanduser("~")
        display_cwd = cwd.replace(home, "~", 1) if cwd.startswith(home) else cwd
        if len(display_cwd) > 40:
            parts = display_cwd.split("/")
            display_cwd = "/".join(parts[-3:]) if len(parts) > 3 else display_cwd

        # Build two-column layout using Rich Table
        from rich.table import Table
        from rich.text import Text

        # Left column: welcome + logo
        logo = Text()
        logo.append("   ^-----^\n", style="bold magenta")
        logo.append("  ( o   o )\n", style="bold magenta")
        logo.append(" (   ( )   )\n", style="bold magenta")
        logo.append("  \  ---  /\n", style="bold magenta")

        welcome = Text()
        welcome.append("  Welcome back!", style="bold")

        left = Text.assemble(logo, welcome)

        # Right column: tips + info
        right = Text()
        right.append(" Tips for getting started\n", style="bold")
        right.append(" Type /help for available commands\n", style="dim")
        right.append(" Type /context to check token usage\n", style="dim")
        right.append(" ─────────────────────────────────\n", style="dim")
        right.append(f" {model}  ·  {display_cwd}", style="")

        # Combine into a panel
        table = Table(show_header=False, box=None, padding=0, expand=False)
        table.add_column(min_width=22)
        table.add_column(min_width=38)
        table.add_row(left, right)

        self.console.print(
            Panel(
                table,
                title="[bold]GG Bond Code[/bold]",
                border_style="blue",
            )
        )
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

    def _print_context_info(self, store: Store) -> None:
        """Print context window details for the current model."""
        from .api.models import get_model_spec
        from .compact.budget import (
            estimate_token_count,
            get_effective_context_window,
            get_auto_compact_threshold,
            calculate_token_warning_state,
        )

        model = self.model or store.get("model", "deepseek-chat")
        spec = get_model_spec(model)
        messages = store.get("messages", [])
        token_usage = estimate_token_count(messages)
        effective = get_effective_context_window(model)
        threshold = get_auto_compact_threshold(model)
        warning_state = calculate_token_warning_state(token_usage, model)

        used_pct = round((token_usage / effective) * 100) if effective > 0 else 0
        bar_len = 30
        filled = min(bar_len, round(bar_len * token_usage / effective)) if effective > 0 else 0
        bar = "█" * filled + "░" * (bar_len - filled)

        # Determine color based on warning state
        if warning_state.is_at_blocking:
            bar_color = "red"
        elif warning_state.is_above_auto_compact:
            bar_color = "yellow"
        elif warning_state.is_above_warning:
            bar_color = "yellow"
        else:
            bar_color = "green"

        lines = [
            f"[bold]Model[/bold]:           {model}",
            f"[bold]Context Window[/bold]:   {spec.context_window:,} tokens",
            f"[bold]Max Output[/bold]:       {spec.max_output_tokens:,} tokens",
            f"[bold]Effective Window[/bold]: {effective:,} tokens",
            f"[bold]Auto-Compact at[/bold]:  {threshold:,} tokens ({round(threshold / effective * 100)}% of effective)",
            f"[bold]Blocking at[/bold]:      {effective - 3000:,} tokens",
            "",
            f"[bold]Token Usage[/bold]:      {token_usage:,} / {effective:,} ({used_pct}%)",
            f"                    [{bar_color}]{bar}[/{bar_color}] {used_pct}%",
            f"[bold]Messages[/bold]:         {len(messages)}",
            "",
            f"[bold]Warning State[/bold]:    {'🔴 Blocking' if warning_state.is_at_blocking else '🟡 Auto-Compact' if warning_state.is_above_auto_compact else '🟡 Warning' if warning_state.is_above_warning else '🟢 OK'}",
            f"[bold]Percent Left[/bold]:     {warning_state.percent_left}%",
        ]

        self.console.print(
            Panel(
                "\n".join(lines),
                title="Context Window",
                border_style=bar_color,
            )
        )

    def _print_help(self) -> None:
        self.console.print(
            Panel(
                "/help     - Show this help\n"
                "/clear    - Clear conversation\n"
                "/compact  - Compact conversation history\n"
                "/context  - Show context window details\n"
                "/thinking - Toggle thinking display\n"
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

    @staticmethod
    def _format_tool_label(name: str, params: dict) -> str:
        """Format a tool call in Claude Code style: ToolName(key_param=value).

        Shows only the most relevant parameter (file_path, command, pattern, etc.)
        instead of all parameters.
        """
        # Pick the most relevant param to show inline
        priority_keys = [
            "file_path", "path", "command", "pattern", "query",
            "url", "name", "description", "content",
        ]
        for key in priority_keys:
            if key in params:
                val = str(params[key])
                if len(val) > 80:
                    val = val[:77] + "..."
                return f"`{name}`({val})"
        # Fallback: show first param
        if params:
            first_key = next(iter(params))
            val = str(params[first_key])
            if len(val) > 80:
                val = val[:77] + "..."
            return f"`{name}`({val})"
        return f"`{name}`()"

    @staticmethod
    def _format_tool_result(name: str, result: str) -> str:
        """Format a tool result summary for display.

        For file tools, shows the file path and line count.
        For other tools, shows a brief summary.
        """
        result = result.strip()
        if not result:
            return "Done"

        # Detect common result patterns
        if result.startswith("Successfully wrote") or result.startswith("Wrote"):
            # Extract file path and line count from Write/Edit results
            lines = result.count("\n") + 1
            # Try to extract file path
            parts = result.split()
            for i, p in enumerate(parts):
                if "/" in p or p.endswith(".py") or p.endswith(".md") or p.endswith(".txt"):
                    return f"Wrote {lines} lines to {p}"
            return f"Wrote {lines} lines"

        # For Read results, count lines
        if name in ("Read", "Grep", "Glob"):
            line_count = result.count("\n") + 1
            if line_count > 3:
                return f"{line_count} lines"

        # Truncate long results
        if len(result) > 100:
            first_line = result.split("\n")[0]
            if len(first_line) > 100:
                return first_line[:97] + "..."
            return first_line

        return result
