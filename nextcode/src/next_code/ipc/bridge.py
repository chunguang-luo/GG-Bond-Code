"""IPC Bridge — adapts QueryRunner events to IPC messages.

The bridge is the key mapping layer between the Python backend
and the Ink frontend:

    QueryRunner.run() → QueryEvent → IPCBridge → IPC Message → Ink

It also handles the reverse direction: Ink user input → IPC message → Python.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .transport import IPCTransport
from .protocol import (
    Message,
    CoreToInk,
    InkToCore,
    QUERY_EVENT_MAP,
    PermissionDecisionValue,
)
from ..query import QueryRunner, QueryEvent
from ..permissions.manager import PermissionDecision
from ..state.store import Store
from ..state.context import create_store_context, ToolUseContext

logger = logging.getLogger(__name__)


class IPCBridge:
    """Bridge between QueryRunner events and IPC transport.

    Translates QueryEvents into IPC messages for the Ink frontend,
    and handles incoming IPC messages (user input, permission responses, etc.).
    """

    def __init__(
        self,
        transport: IPCTransport,
        model: str | None = None,
        *,
        allowed_tools: str | None = None,
        disallowed_tools: str | None = None,
        permission_mode: str | None = None,
    ) -> None:
        self.transport = transport
        self.model = model

        # Query runner with IPC permission callback
        self._context = create_store_context()
        self._runner = QueryRunner(
            model=model,
            permission_callback=self._ask_permission,
            context=self._context,
            enable_streaming_tools=True,
        )

        # Apply CLI permission configuration
        self._apply_cli_permissions(allowed_tools, disallowed_tools, permission_mode)

        # Command dispatcher
        from ..commands import create_builtin_registry, CommandDispatcher
        self._command_registry = create_builtin_registry()
        self._command_dispatcher = CommandDispatcher(self._command_registry)

        # Wire command registry into ToolUseContext so SkillTool can access it
        self._context.command_registry = self._command_registry

        # Pending permission requests: requestId → Future
        self._pending_permissions: dict[str, asyncio.Future[PermissionDecision]] = {}

        # Tool start times for elapsed tracking
        self._tool_start_times: dict[str, float] = {}

        # Agent start times for elapsed tracking
        self._agent_start_times: dict[str, float] = {}

        # Track if a query is running
        self._query_task: asyncio.Task | None = None

        # Message queue: user messages submitted while a query is running
        # are enqueued and processed in order after the current query completes.
        self._message_queue: list[str] = []
        self._is_query_running = False

        # Load skills (async — fire and forget, skills register when ready)
        self._skills_loaded = False

        # Conditional skill activation
        from ..skills.conditional import ConditionalSkillManager
        self._conditional_manager = ConditionalSkillManager()

        # Register task poller to send IPC events for frontend UI
        self._register_task_poller()

        # Register this bridge's stall handler as the global callback
        # Use late binding to avoid circular import — the function is defined
        # later in this same file, but by the time __init__ runs it's available.
        set_stall_notification_callback(self._handle_stall)

        # Register message handler
        self.transport.on_message(self._handle_message)

    @property
    def runner(self) -> QueryRunner:
        return self._runner

    def _apply_cli_permissions(
        self,
        allowed_tools: str | None,
        disallowed_tools: str | None,
        permission_mode: str | None,
    ) -> None:
        """Apply CLI-specified permission configuration to the PermissionManager.

        --allowedTools adds to the allow list for this session.
        --disallowedTools adds to the deny list for this session.
        --permission-mode sets the global permission mode.
        """
        pm = self._context.permissions

        if allowed_tools:
            for tool in allowed_tools.split(","):
                tool = tool.strip()
                if tool and tool not in pm._session_allowed:
                    pm._session_allowed.add(tool)

        if disallowed_tools:
            for tool in disallowed_tools.split(","):
                tool = tool.strip()
                if tool and tool not in pm._denied:
                    pm._denied.append(tool)

        if permission_mode:
            from ..permissions.modes import PermissionMode
            pm.mode = PermissionMode(permission_mode)

    async def _handle_message(self, msg: Message) -> None:
        """Handle incoming messages from Ink frontend."""
        try:
            if msg.type == InkToCore.USER_MESSAGE:
                await self._handle_user_message(msg.payload.get("text", ""))
            elif msg.type == InkToCore.USER_INTERRUPT:
                await self._handle_interrupt()
            elif msg.type == InkToCore.USER_COMMAND:
                await self._handle_command(msg.payload.get("command", ""))
            elif msg.type == InkToCore.PERMISSION_RESPONSE:
                await self._handle_permission_response(msg)
            elif msg.type == InkToCore.UI_TOGGLE_THINKING:
                Store().set("ui.show_thinking", msg.payload.get("enabled", False))
            elif msg.type == InkToCore.THEME_CHANGE:
                Store().set("ui.theme", msg.payload.get("theme", "dark"))
            elif msg.type == InkToCore.PONG:
                pass  # Heartbeat response, no action needed
            elif msg.type == InkToCore.READY:
                logger.info("IPC: Ink frontend ready: %s", msg.payload)
            elif msg.type == InkToCore.SHUTDOWN_ACK:
                logger.info("IPC: Ink acknowledged shutdown")
            elif msg.type == InkToCore.TASK_STOP:
                task_id = msg.payload.get("task_id", "")
                await self._handle_task_stop(task_id)
            elif msg.type == InkToCore.TASK_RETAIN:
                task_id = msg.payload.get("task_id", "")
                retain = msg.payload.get("retain", False)
                await self._handle_task_retain(task_id, retain)
            elif msg.type == InkToCore.PERMISSION_MODE_CYCLE:
                await self._handle_permission_mode_cycle()
            else:
                logger.warning("IPC: unknown message type: %s", msg.type)
        except Exception:
            logger.exception("IPC: error handling message %s", msg.type)

    # ── User input handling ────────────────────────────────────────────────

    async def _handle_user_message(self, text: str) -> None:
        """Handle a user message from Ink.

        If a query is currently running, the message is enqueued and will
        be processed after the current query completes. Otherwise, a new
        query is started immediately.
        """
        if not text.strip():
            return

        if self._is_query_running:
            # Enqueue the message for later processing
            self._message_queue.append(text)
            await self.transport.send_event(CoreToInk.QUERY_QUEUED.value, {"text": text})
            logger.info("IPC: query queued (queue depth: %d)", len(self._message_queue))
        else:
            self._query_task = asyncio.create_task(self._run_query(text))

    async def _handle_interrupt(self) -> None:
        """Handle Ctrl+C from Ink: cancel the current query."""
        if self._query_task is not None and not self._query_task.done():
            self._query_task.cancel()

    async def _handle_command(self, command: str) -> None:
        """Handle a slash command from Ink via the dispatcher."""
        from ..commands.types import CommandContext, ResultType
        from ..context.system import clear_system_context_cache

        store = Store()
        context = CommandContext(
            model=self.model,
            store_get=store.get,
            store_set=store.set,
            loop_state=self._runner.loop_state,
            clear_system_context_cache=clear_system_context_cache,
            registry=self._command_registry,
        )

        # For /compact, send compact.started before dispatch
        cmd_name = command.strip().lower().split()[0]
        if cmd_name == "/compact":
            await self.transport.send_event(CoreToInk.COMPACT_STARTED, {"level": "full"})

        result = await self._command_dispatcher.dispatch(command, context)

        # Interpret result for IPC output
        if result.type == ResultType.TEXT:
            # Support both 'message' and 'content' fields
            message = result.content.get("message")
            if message is None:
                message = result.content.get("content", "")
            # Optionally prepend title
            if "title" in result.content and message:
                message = f"**{result.content['title']}**\n\n{message}"
            await self.transport.send_event("query.info", {"message": message})
        elif result.type == ResultType.CLEAR:
            self._message_queue.clear()
            self._context = create_store_context()
            self._runner = QueryRunner(
                model=self.model,
                permission_callback=self._ask_permission,
                context=self._context,
                enable_streaming_tools=True,
            )
            await self.transport.send_event("query.cleared", {})
        elif result.type == ResultType.SHUTDOWN:
            await self.transport.send_event(CoreToInk.SESSION_SHUTDOWN, result.content)
        elif result.type == ResultType.CONTEXT_INFO:
            await self.transport.send_event(CoreToInk.CONTEXT_INFO, result.content)
        elif result.type == ResultType.COMPACT_COMPLETE:
            await self.transport.send_event(CoreToInk.COMPACT_COMPLETE, result.content)
            # Auto-update context bar after compact changes token usage
            await self._send_auto_context_info()
        elif result.type == ResultType.UNKNOWN_COMMAND:
            hint = f"Unknown command: {result.content['command']}"
            if result.content.get("suggestion"):
                hint += f"\nDid you mean: {result.content['suggestion']}?"
            await self.transport.send_event("query.info", {"message": hint})
        elif result.type == ResultType.PROMPT:
            # Skill command: inject the generated prompt as a user query
            prompt_blocks = result.content.get("prompt_blocks", [])
            prompt_text = "\n".join(
                block.get("text", "") for block in prompt_blocks if block.get("type") == "text"
            )
            if prompt_text:
                self._query_task = asyncio.create_task(self._run_query(prompt_text))

    # ── Query execution ────────────────────────────────────────────────────

    async def _run_query(self, user_message: str) -> None:
        """Run a query and stream events to Ink via IPC."""
        self._tool_start_times.clear()
        self._agent_start_times.clear()
        self._is_query_running = True

        # Inject direct IPC emit callback so long-running sub-tools
        # (e.g. AgentTool) can stream events in real-time without
        # waiting for the parent QueryRunner to yield tool_result.
        self._context.emit_ipc = self._emit_event

        try:
            async for event in self._runner.run(user_message):
                await self._emit_event(event)
            # Query completed normally — send COMPLETE event
            reason = self._runner.loop_state.transition.value if self._runner.loop_state.transition else "done"

            # Auto-send context info for status bar updates
            await self._send_auto_context_info()

            await self.transport.send_event(CoreToInk.QUERY_COMPLETE.value, {
                "transitionReason": reason,
                "turnCount": self._runner.loop_state.turn_count,
            })
        except asyncio.CancelledError:
            await self.transport.send_event(CoreToInk.QUERY_COMPLETE.value, {
                "transitionReason": "cancelled",
                "turnCount": self._runner.loop_state.turn_count,
            })
        except Exception as e:
            logger.exception("IPC: query error")
            await self.transport.send_event(CoreToInk.QUERY_ERROR.value, {"content": str(e)})
        finally:
            self._is_query_running = False
            # Process next message in queue if any
            await self._process_queue()

    async def _process_queue(self) -> None:
        """Process the next queued user message, if any."""
        if self._message_queue:
            next_message = self._message_queue.pop(0)
            # Notify frontend that the queued message is now being processed
            await self.transport.send_event(CoreToInk.QUERY_DEQUEUE.value, {"text": next_message})
            self._query_task = asyncio.create_task(self._run_query(next_message))

    async def _send_auto_context_info(self) -> None:
        """Send context info automatically for status bar updates.

        Unlike the /context command, this is a lightweight update that
        only refreshes the status bar — it doesn't display detailed info
        in the message list. The `auto: true` flag tells the frontend
        to only update the context bar state without toggling or showing details.
        """
        try:
            from ..api.models import get_model_spec, get_context_window_for_model, get_max_output_tokens_for_model
            from ..compact.budget import (
                estimate_token_count,
                get_effective_context_window,
                get_auto_compact_threshold,
                calculate_token_warning_state,
            )

            model = self.model or self._context.get_state("model")
            spec = get_model_spec(model)
            # Use self._context to get messages, not a separate Store instance
            messages = self._context.get_state("messages") or []
            token_usage = estimate_token_count(messages)
            effective = get_effective_context_window(model)
            warning_state = calculate_token_warning_state(token_usage, model)

            await self.transport.send_event(CoreToInk.CONTEXT_INFO.value, {
                "model": model,
                "contextWindow": spec.context_window,
                "maxOutputTokens": spec.max_output_tokens,
                "tokenUsage": token_usage,
                "effectiveWindow": effective,
                "autoCompactThreshold": get_auto_compact_threshold(model),
                "blockingAt": effective - 3000,
                "messageCount": len(messages),
                "warningState": (
                    "blocking" if warning_state.is_at_blocking
                    else "auto_compact" if warning_state.is_above_auto_compact
                    else "warning" if warning_state.is_above_warning
                    else "ok"
                ),
                "percentLeft": warning_state.percent_left,
                "auto": True,
            })
        except Exception:
            logger.debug("Auto context info failed (non-critical)", exc_info=True)

    # Mapping from sub-agent event types to agent-specific IPC message types.
    # Events with source="agent" use these instead of the parent QUERY_EVENT_MAP,
    # so the frontend can render sub-agent output distinctly.
    _AGENT_EVENT_MAP: dict[str, str] = {
        "text": CoreToInk.AGENT_TEXT_DELTA.value,
        "tool_use": CoreToInk.AGENT_TOOL_USE.value,
        "tool_result": CoreToInk.AGENT_TOOL_RESULT.value,
        "agent_start": CoreToInk.AGENT_START.value,
        "agent_result": CoreToInk.AGENT_RESULT.value,
        "agent_progress": CoreToInk.AGENT_PROGRESS.value,
        "error": CoreToInk.QUERY_ERROR.value,
    }

    async def _emit_event(self, event: QueryEvent) -> None:
        """Translate a QueryEvent into an IPC message and send it."""
        # Sub-agent events use the agent-specific map
        if event.source == "agent":
            msg_type = self._AGENT_EVENT_MAP.get(event.type)
            logger.debug("IPC: agent event type=%s → msg_type=%s", event.type, msg_type)
        else:
            msg_type = QUERY_EVENT_MAP.get(event.type)
        if msg_type is None:
            logger.warning("IPC: unmapped event type: %s (source=%s)", event.type, event.source)
            return

        payload: dict[str, Any] = {}

        if event.type == "text":
            payload = {"text": event.content}

        elif event.type == "thinking":
            payload = {"text": event.content}

        elif event.type == "tool_start":
            if event.tool_use_id:
                self._tool_start_times[event.tool_use_id] = time.monotonic()
            payload = {"toolUseId": event.tool_use_id, "toolName": event.tool_name}

        elif event.type == "tool_use":
            if event.tool_use_id and event.tool_use_id not in self._tool_start_times:
                self._tool_start_times[event.tool_use_id] = time.monotonic()
            payload = {
                "toolUseId": event.tool_use_id,
                "toolName": event.tool_name,
                "toolInput": event.tool_input,
                "toolPurpose": event.tool_purpose,
            }
            # Check conditional skill activation after file operation tools
            self._check_conditional_activation(event.tool_name, event.tool_input)

        elif event.type == "tool_result":
            elapsed_ms = 0
            if event.tool_use_id and event.tool_use_id in self._tool_start_times:
                t0 = self._tool_start_times.pop(event.tool_use_id)
                elapsed_ms = (time.monotonic() - t0) * 1000
            payload = {
                "toolUseId": event.tool_use_id,
                "toolName": event.tool_name,
                "toolResult": event.tool_result,
                "toolError": event.tool_error,
                "elapsedMs": round(elapsed_ms),
                "toolMetadata": event.metadata,
            }

        elif event.type == "error":
            payload = {"content": event.content}

        elif event.type == "warning":
            payload = {"content": event.content, "metadata": event.metadata}

        elif event.type == "agent_start":
            agent_id = event.metadata.get("agent_id", "")
            if agent_id:
                self._agent_start_times[agent_id] = time.monotonic()
            payload = {
                "agent_id": agent_id,
                "agent_type": event.metadata.get("agent_type", "unknown"),
                "description": event.metadata.get("description", ""),
                "prompt": event.metadata.get("prompt", ""),
            }

        elif event.type == "agent_result":
            agent_id = event.metadata.get("agent_id", "")
            elapsed_sec = 0
            if agent_id and agent_id in self._agent_start_times:
                t0 = self._agent_start_times.pop(agent_id)
                elapsed_sec = time.monotonic() - t0
            # Format elapsed as "Xm Ys" or "Ys"
            if elapsed_sec >= 60:
                m = int(elapsed_sec // 60)
                s = int(elapsed_sec % 60)
                elapsed_str = f"{m}m {s}s"
            else:
                elapsed_str = f"{int(elapsed_sec)}s"
            payload = {
                "content": event.content,
                "agent_id": agent_id,
                "agent_type": event.metadata.get("agent_type", "unknown"),
                "elapsed": elapsed_str,
                "tool_use_count": event.metadata.get("tool_use_count", 0),
            }

        elif event.type == "agent_progress":
            payload = {
                "agent_id": event.metadata.get("agent_id", ""),
                "tool_use_count": event.metadata.get("tool_use_count", 0),
            }

        # msg_type is either a CoreToInk enum (parent events) or a str (agent events)
        msg_type_str = msg_type.value if hasattr(msg_type, "value") else msg_type
        await self.transport.send_event(msg_type_str, payload)

    # ── Conditional skill activation ────────────────────────────────────────

    # Tools that operate on files and should trigger conditional skill checks
    _FILE_TOOLS = frozenset({
        "Bash", "Read", "Edit", "Write", "Glob", "Grep",
    })

    # Parameter keys that carry file paths for each tool
    _FILE_PARAM_KEYS: dict[str, list[str]] = {
        "Read": ["file_path"],
        "Edit": ["file_path"],
        "Write": ["file_path"],
        "Glob": ["path"],
        "Grep": ["path"],
        "Bash": ["command"],  # will extract paths from command string heuristically
    }

    def _check_conditional_activation(self, tool_name: str, tool_input: dict) -> None:
        """After a file tool executes, check if any pending skills should activate."""
        if tool_name not in self._FILE_TOOLS:
            return
        if not self._conditional_manager.get_pending():
            return

        file_paths = self._extract_file_paths(tool_name, tool_input)
        if not file_paths:
            return

        cwd = Store().get("cwd", "")
        activated = self._conditional_manager.activate_for_paths(file_paths, cwd)

        if activated:
            # Discover any new skill directories near the activated paths
            from ..skills.loader import discover_skill_dirs_for_paths
            discovered = discover_skill_dirs_for_paths(file_paths, cwd)
            if discovered:
                asyncio.create_task(self._load_discovered_skills(discovered))

    def _extract_file_paths(self, tool_name: str, tool_input: dict) -> list[str]:
        """Extract file paths from tool input parameters."""
        paths: list[str] = []
        param_keys = self._FILE_PARAM_KEYS.get(tool_name, [])

        for key in param_keys:
            value = tool_input.get(key, "")
            if isinstance(value, str) and value:
                if tool_name == "Bash":
                    # For Bash, we don't try to extract paths from commands
                    # (too unreliable). Just skip.
                    continue
                paths.append(value)
            elif isinstance(value, list):
                paths.extend(str(v) for v in value if v)

        return paths

    async def _load_discovered_skills(self, skill_dirs: list) -> None:
        """Load skills from newly discovered directories."""
        from ..skills.loader import load_skills_from_dir
        from ..commands.types import PromptCommand

        for skill_dir in skill_dirs:
            commands = await load_skills_from_dir(skill_dir, source="discovered", loaded_from="skills")
            for cmd in commands:
                try:
                    self._command_registry.register_override(cmd)
                    if isinstance(cmd, PromptCommand) and cmd.paths:
                        self._conditional_manager.register_conditional(cmd)
                except ValueError:
                    pass  # already registered

        from ..commands.cache import clear_command_caches
        clear_command_caches()

    # ── Permission flow ────────────────────────────────────────────────────

    async def _ask_permission(self, tool_name: str, params: dict) -> PermissionDecision:
        """IPC permission callback: send request to Ink, wait for response."""
        request_id = f"perm-{id(asyncio.get_event_loop())}-{time.monotonic_ns()}"
        future: asyncio.Future[PermissionDecision] = asyncio.get_event_loop().create_future()
        self._pending_permissions[request_id] = future

        # Send permission request to Ink
        await self.transport.send_event(CoreToInk.PERMISSION_REQUEST.value, {
            "requestId": request_id,
            "toolName": tool_name,
            "params": params,
        })

        # Wait for response with timeout (3 minutes for slow operations)
        try:
            return await asyncio.wait_for(future, timeout=180.0)
        except asyncio.TimeoutError:
            logger.warning("IPC: permission request %s timed out", request_id)
            return PermissionDecision.DENY
        finally:
            self._pending_permissions.pop(request_id, None)

    async def _handle_permission_response(self, msg: Message) -> None:
        """Resolve a pending permission request Future."""
        request_id = msg.payload.get("requestId", "")
        decision_str = msg.payload.get("decision", "deny")
        wildcard = msg.payload.get("wildcard", False)
        logger.info("Permission response: decision=%s, wildcard=%s, tool=%s", decision_str, wildcard, msg.payload.get("toolName", ""))

        future = self._pending_permissions.get(request_id)
        if future is None or future.done():
            logger.warning("IPC: unknown or expired permission request: %s", request_id)
            return

        # Map IPC decision to PermissionDecision
        if decision_str == PermissionDecisionValue.ALWAYS_ALLOW.value:
            tool_name = msg.payload.get("toolName", "")
            params = msg.payload.get("params", {})
            self._runner.permissions.grant_session(tool_name, params, wildcard=wildcard)
            future.set_result(PermissionDecision.ALLOW)
        elif decision_str == PermissionDecisionValue.ALLOW.value:
            future.set_result(PermissionDecision.ALLOW)
        else:
            future.set_result(PermissionDecision.DENY)

    async def _handle_permission_mode_cycle(self) -> None:
        """Handle permission mode cycle from frontend — switch to next mode."""
        from ..permissions.modes import PermissionMode, get_next_permission_mode

        pm = self._context.permissions
        current = pm.mode or PermissionMode.DEFAULT
        next_mode = get_next_permission_mode(current, bypass_available=True)
        pm.mode = next_mode
        logger.info("IPC: permission mode cycled to %s", next_mode.value)

        # Notify frontend of the new mode
        await self.transport.send_event(CoreToInk.PERMISSION_MODE_UPDATE.value, {
            "mode": next_mode.value,
        })

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def send_welcome(self) -> None:
        """Send welcome screen data to Ink."""
        store = Store()
        model = self.model or store.get("model", "unknown")
        cwd = store.get("cwd", "unknown")

        await self.transport.send_event(CoreToInk.WELCOME.value, {
            "model": model,
            "cwd": cwd,
        })

    async def send_session_ready(self) -> None:
        """Send session ready notification."""
        store = Store()
        model = self.model or store.get("model", "unknown")
        cwd = store.get("cwd", "unknown")

        await self.transport.send_event(CoreToInk.SESSION_READY.value, {
            "model": model,
            "cwd": cwd,
            "projectRoot": cwd,
        })

        # Send initial command list (builtins only at this point)
        await self._send_commands_update()

        # Load skills after session is ready (non-blocking)
        if not self._skills_loaded:
            self._skills_loaded = True
            asyncio.create_task(self._load_skills(cwd))

    async def _load_skills(self, cwd: str) -> None:
        """Load skills from user and project directories."""
        try:
            await self._command_registry.load_skills(cwd)
            # Register conditional skills (those with paths) in the manager
            from ..commands.types import PromptCommand
            for cmd in self._command_registry.all_commands():
                if isinstance(cmd, PromptCommand) and cmd.paths:
                    self._conditional_manager.register_conditional(cmd)
            # Clear command caches after skill set changes
            from ..commands.cache import clear_command_caches
            clear_command_caches()
            # Send updated command list to frontend (includes skills now)
            await self._send_commands_update()
            logger.info("Skills loaded for cwd=%s", cwd)
        except Exception:
            logger.exception("Failed to load skills")

    async def _send_commands_update(self) -> None:
        """Send the current command list to the frontend."""
        from ..commands.types import PromptCommand

        commands = []
        for cmd in self._command_registry.all_commands():
            # Only include user-invocable, non-hidden commands
            if not getattr(cmd, "user_invocable", True):
                continue
            if getattr(cmd, "is_hidden", False):
                continue
            entry = {
                "name": cmd.name,
                "description": cmd.description,
                "source": getattr(cmd, "source", "builtin"),
                "aliases": list(cmd.aliases),
            }
            # Include when_to_use hint for conditional skills that are activated
            if isinstance(cmd, PromptCommand) and cmd.when_to_use:
                entry["whenToUse"] = cmd.when_to_use
            commands.append(entry)

        await self.transport.send_event(CoreToInk.COMMANDS_UPDATE.value, {
            "commands": commands,
        })

    # ── Task polling for frontend UI ─────────────────────────────────────

    def _register_task_poller(self) -> None:
        """Register a periodic poll to send task IPC events to the frontend.

        Sends TASK_STARTED / TASK_COMPLETED / TASK_FAILED events and task count
        updates so the frontend can display background task notifications.
        Also streams real-time output for running tasks via TASK_OUTPUT events.

        The model-side waiting is handled by QueryRunner._await_background_tasks().
        """
        from ..tasks.registry import get_task_registry
        from ..tasks.types import TaskStatus, TaskType
        from ..tasks.disk_output import DiskTaskOutput

        seen_task_ids: set[str] = set()
        task_output_cache: dict[str, str] = {}  # task_id -> last output for change detection

        async def _poll_tasks() -> None:
            while True:
                await asyncio.sleep(0.5)  # Poll every 500ms for output updates
                try:
                    registry = get_task_registry()

                    # Send running task count (every 2s, not every 500ms)
                    if not hasattr(_poll_tasks, "_last_count_update"):
                        _poll_tasks._last_count_update = 0.0
                    now = time.monotonic()
                    if now - _poll_tasks._last_count_update > 2.0:
                        bash_running, agent_running = registry.running_count()
                        # Count completed (non-evicted) tasks for progress display
                        bash_done = 0
                        agent_done = 0
                        for t in registry.list_all():
                            if t.is_terminal():
                                if t.type == TaskType.LOCAL_BASH:
                                    bash_done += 1
                                elif t.type == TaskType.LOCAL_AGENT:
                                    agent_done += 1
                        # Skip sending if counts haven't changed
                        new_counts = (bash_running, agent_running, bash_done, agent_done)
                        if not hasattr(_poll_tasks, "_last_counts"):
                            _poll_tasks._last_counts = None
                        if new_counts != _poll_tasks._last_counts:
                            _poll_tasks._last_counts = new_counts
                            await self.transport.send_event(
                                CoreToInk.TASK_COUNT.value,
                                {
                                    "bash": bash_running,
                                    "agent": agent_running,
                                    "bash_done": bash_done,
                                    "agent_done": agent_done,
                                },
                            )
                        _poll_tasks._last_count_update = now

                    # Announce newly started tasks
                    for task in registry.list_all():
                        if task.id not in seen_task_ids:
                            seen_task_ids.add(task.id)
                            if task.status == TaskStatus.RUNNING:
                                desc = task.description or task.command[:50]
                                await self.transport.send_event(
                                    CoreToInk.TASK_STARTED.value,
                                    {
                                        "task_id": task.id,
                                        "task_type": task.type.value,
                                        "description": desc,
                                    },
                                )

                    # Stream output for running tasks
                    for task in registry.list_all():
                        if task.status != TaskStatus.RUNNING:
                            continue
                        if not task._output_path:
                            continue

                        # Read tail from disk
                        disk_output = DiskTaskOutput(task._output_path)
                        tail = disk_output.read_tail(lines=30)

                        # Only send if output changed
                        last_output = task_output_cache.get(task.id, "")
                        if tail != last_output:
                            task_output_cache[task.id] = tail
                            await self.transport.send_event(
                                CoreToInk.TASK_OUTPUT.value,
                                {
                                    "task_id": task.id,
                                    "output": tail,
                                    "is_running": True,
                                },
                            )

                    # Announce newly completed tasks
                    for task in registry.list_all():
                        if task.is_terminal() and not task.notified:
                            if registry.mark_notified(task.id):
                                event_type = (
                                    CoreToInk.TASK_COMPLETED.value
                                    if task.status == TaskStatus.COMPLETED
                                    else CoreToInk.TASK_FAILED.value
                                )
                                # Send final output
                                final_output = ""
                                if task._output_path:
                                    disk_output = DiskTaskOutput(task._output_path)
                                    final_output = disk_output.read_all()
                                result_preview = final_output or (task.result or "")[:2000]
                                desc = task.description or task.command[:50]

                                # Clear output cache
                                task_output_cache.pop(task.id, None)

                                await self.transport.send_event(event_type, {
                                    "task_id": task.id,
                                    "task_type": task.type.value,
                                    "status": task.status.value,
                                    "result": result_preview,
                                    "description": desc,
                                })

                    # Evict old terminal tasks
                    registry.evict_terminal()

                except Exception:
                    logger.exception("Task poll error")

        asyncio.create_task(_poll_tasks())

    # ── Stall watchdog callback ───────────────────────────────────────────────

    async def _handle_stall(self, task_id: str, prompt_type: str) -> None:
        """Handle stall detection — send notification to frontend.

        When a background task has been silent for 45+ seconds and the
        last output line matches a known interactive prompt pattern
        (e.g., apt confirmation, sudo password), this notifies the model
        so it can decide whether to kill the task or send input.
        """
        logger.info("Stall detected for task %s: %s", task_id, prompt_type)

        await self.transport.send_event(
            CoreToInk.TASK_STALLED.value,
            {
                "task_id": task_id,
                "prompt_type": prompt_type,
                "message": f"Task {task_id} may be waiting for user input: {prompt_type}",
            },
        )

    # ── Task control handlers ────────────────────────────────────────────────

    async def _handle_task_stop(self, task_id: str) -> None:
        """Handle TASK_STOP from frontend — stop a running task."""
        from ..tasks.registry import get_task_registry
        from ..tasks.stall_watchdog import stop_watchdog

        registry = get_task_registry()
        task = registry.get(task_id)
        if task is None:
            logger.warning("IPC: TASK_STOP for unknown task %s", task_id)
            return

        logger.info("IPC: Stopping task %s", task_id)

        # Stop the stall watchdog
        stop_watchdog(task_id)

        # Kill the subprocess if running
        if task._process is not None:
            try:
                task._process.terminate()
                # Give it a moment to gracefully terminate
                try:
                    await asyncio.wait_for(task._process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    # Force kill if it doesn't terminate gracefully
                    task._process.kill()
            except Exception:
                pass

        # Cancel the asyncio task if running
        if task._asyncio_task is not None and not task._asyncio_task.done():
            task._asyncio_task.cancel()

        # Update task status
        registry.update(task_id, status="killed", result="Stopped by user")

    async def _handle_task_retain(self, task_id: str, retain: bool) -> None:
        """Handle TASK_RETAIN from frontend — mark a task to stay visible."""
        from ..tasks.registry import get_task_registry

        registry = get_task_registry()
        success = registry.mark_retain(task_id, retain)
        if success:
            logger.info("IPC: Task %s retain=%s", task_id, retain)
        else:
            logger.warning("IPC: TASK_RETAIN for unknown task %s", task_id)


# ── Global stall callback registry ───────────────────────────────────────────


# Global reference to the current bridge's stall handler
_global_stall_callback: callable | None = None


def get_stall_notification_callback() -> callable | None:
    """Get the global stall notification callback."""
    return _global_stall_callback


def set_stall_notification_callback(callback: callable | None) -> None:
    """Set the global stall notification callback (called by IPCBridge.__init__)."""
    global _global_stall_callback
    _global_stall_callback = callback
