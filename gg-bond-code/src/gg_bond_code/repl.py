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

        # Command dispatcher
        from .commands import create_builtin_registry, CommandDispatcher
        self._command_registry = create_builtin_registry()
        self._command_dispatcher = CommandDispatcher(self._command_registry)

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
                    text_output.append(f"\n\n - {tool_label}")

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
        """Handle slash commands via the dispatcher. Returns True if should continue REPL."""
        from .commands.types import CommandContext, ResultType

        store = Store()
        context = CommandContext(
            model=self.model,
            store_get=store.get,
            store_set=store.set,
            loop_state=self.runner.loop_state,
            clear_system_context_cache=clear_system_context_cache,
            registry=self._command_registry,
        )

        # For /compact, print "Compacting..." before dispatch
        cmd_name = command.strip().lower().split()[0]
        if cmd_name == "/compact":
            self.console.print("[dim]Compacting conversation...[/dim]")

        result = await self._command_dispatcher.dispatch(command, context)

        # Interpret result for Rich output
        if result.type == ResultType.TEXT:
            self.console.print(result.content["message"])
        elif result.type == ResultType.CLEAR:
            self._context = create_store_context()
            self.runner = QueryRunner(
                model=self.model,
                permission_callback=self._ask_permission,
                context=self._context,
                enable_streaming_tools=True,
            )
            self.console.clear()
            self._print_welcome()
        elif result.type == ResultType.SHUTDOWN:
            self.running = False
            return False
        elif result.type == ResultType.CONTEXT_INFO:
            self._render_context_info(result.content)
        elif result.type == ResultType.COMPACT_COMPLETE:
            self.console.print(f"[green]Compacted: {result.content['reason']}[/green]")
        elif result.type == ResultType.UNKNOWN_COMMAND:
            hint = f"Unknown command: {result.content['command']}"
            if result.content.get("suggestion"):
                hint += f"\nDid you mean: [green]{result.content['suggestion']}[/green]?"
            self.console.print(hint, style="yellow")

        return False

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

    def _render_context_info(self, data: dict) -> None:
        """Render context info using Rich formatting from a data dict."""
        token_usage = data.get("tokenUsage", 0)
        effective = data.get("effectiveWindow", 1)
        used_pct = round((token_usage / effective) * 100) if effective > 0 else 0
        bar_len = 30
        filled = min(bar_len, round(bar_len * token_usage / effective)) if effective > 0 else 0
        bar = "█" * filled + "░" * (bar_len - filled)

        warning_state_str = data.get("warningState", "ok")
        if warning_state_str == "blocking":
            bar_color = "red"
        elif warning_state_str in ("auto_compact", "warning"):
            bar_color = "yellow"
        else:
            bar_color = "green"

        model = data.get("model", "unknown")
        context_window = data.get("contextWindow", 0)
        max_output = data.get("maxOutputTokens", 0)
        threshold = data.get("autoCompactThreshold", 0)
        blocking_at = data.get("blockingAt", 0)
        message_count = data.get("messageCount", 0)
        percent_left = data.get("percentLeft", 100)

        state_icon = {"blocking": "🔴 Blocking", "auto_compact": "🟡 Auto-Compact", "warning": "🟡 Warning"}.get(warning_state_str, "🟢 OK")

        lines = [
            f"[bold]Model[/bold]:           {model}",
            f"[bold]Context Window[/bold]:   {context_window:,} tokens",
            f"[bold]Max Output[/bold]:       {max_output:,} tokens",
            f"[bold]Effective Window[/bold]: {effective:,} tokens",
            f"[bold]Auto-Compact at[/bold]:  {threshold:,} tokens ({round(threshold / effective * 100) if effective else 0}% of effective)",
            f"[bold]Blocking at[/bold]:      {blocking_at:,} tokens",
            "",
            f"[bold]Token Usage[/bold]:      {token_usage:,} / {effective:,} ({used_pct}%)",
            f"                    [{bar_color}]{bar}[/{bar_color}] {used_pct}%",
            f"[bold]Messages[/bold]:         {message_count}",
            "",
            f"[bold]Warning State[/bold]:    {state_icon}",
            f"[bold]Percent Left[/bold]:     {percent_left}%",
        ]

        self.console.print(
            Panel(
                "\n".join(lines),
                title="Context Window",
                border_style=bar_color,
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
