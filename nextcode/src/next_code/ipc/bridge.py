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

        # Load skills (async — fire and forget, skills register when ready)
        self._skills_loaded = False

        # Conditional skill activation
        from ..skills.conditional import ConditionalSkillManager
        self._conditional_manager = ConditionalSkillManager()

        # Register message handler
        self.transport.on_message(self._handle_message)

    @property
    def runner(self) -> QueryRunner:
        return self._runner

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
            else:
                logger.warning("IPC: unknown message type: %s", msg.type)
        except Exception:
            logger.exception("IPC: error handling message %s", msg.type)

    # ── User input handling ────────────────────────────────────────────────

    async def _handle_user_message(self, text: str) -> None:
        """Handle a user message from Ink: start a new query."""
        if not text.strip():
            return

        # Cancel any running query
        if self._query_task is not None and not self._query_task.done():
            self._query_task.cancel()

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
            await self.transport.send_event("query.info", {"message": result.content["message"]})
        elif result.type == ResultType.CLEAR:
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

        # Inject direct IPC emit callback so long-running sub-tools
        # (e.g. AgentTool) can stream events in real-time without
        # waiting for the parent QueryRunner to yield tool_result.
        self._context.emit_ipc = self._emit_event

        try:
            async for event in self._runner.run(user_message):
                await self._emit_event(event)
            # Query completed normally — send COMPLETE event
            reason = self._runner.loop_state.transition.value if self._runner.loop_state.transition else "done"
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

    # Mapping from sub-agent event types to agent-specific IPC message types.
    # Events with source="agent" use these instead of the parent QUERY_EVENT_MAP,
    # so the frontend can render sub-agent output distinctly.
    _AGENT_EVENT_MAP: dict[str, str] = {
        "text": CoreToInk.AGENT_TEXT_DELTA.value,
        "tool_use": CoreToInk.AGENT_TOOL_USE.value,
        "tool_result": CoreToInk.AGENT_TOOL_RESULT.value,
        "agent_start": CoreToInk.AGENT_START.value,
        "agent_result": CoreToInk.AGENT_RESULT.value,
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

        # Wait for response with timeout
        try:
            return await asyncio.wait_for(future, timeout=30.0)
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
