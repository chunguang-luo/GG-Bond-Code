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

        # Pending permission requests: requestId → Future
        self._pending_permissions: dict[str, asyncio.Future[PermissionDecision]] = {}

        # Tool start times for elapsed tracking
        self._tool_start_times: dict[str, float] = {}

        # Track if a query is running
        self._query_task: asyncio.Task | None = None

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
        """Handle a slash command from Ink."""
        from ..context.system import clear_system_context_cache
        from ..compact.manager import CompactManager, CompactLevel

        store = Store()
        cmd = command.strip().lower()

        if cmd in ("/exit", "/quit", "/q"):
            await self.transport.send_event(CoreToInk.SESSION_SHUTDOWN, {"reason": "user exit"})
        elif cmd == "/clear":
            clear_system_context_cache()
            store.set("messages", [])
            self._context = create_store_context()
            self._runner = QueryRunner(
                model=self.model,
                permission_callback=self._ask_permission,
                context=self._context,
                enable_streaming_tools=True,
            )
            await self.transport.send_event("query.cleared", {})
        elif cmd == "/compact":
            clear_system_context_cache()
            await self.transport.send_event(CoreToInk.COMPACT_STARTED, {"level": "full"})
            messages = store.get("messages", [])
            if messages:
                manager = CompactManager(model=self.model or store.get("model", "deepseek-chat"))
                compacted, reason = await manager.execute(CompactLevel.FULL, messages)
                store.set("messages", compacted)
                await self.transport.send_event(CoreToInk.COMPACT_COMPLETE, {"reason": reason})
        elif cmd == "/thinking":
            current = store.get("ui.show_thinking", False)
            store.set("ui.show_thinking", not current)
        elif cmd == "/model":
            model = store.get("model", "unknown")
            await self.transport.send_event("query.info", {"message": f"Current model: {model}"})
        elif cmd == "/context":
            await self._send_context_info()
        elif cmd == "/help":
            help_text = (
                "Available commands:\n"
                "  /help     - Show this help message\n"
                "  /clear    - Clear conversation history\n"
                "  /compact  - Compact conversation to save context\n"
                "  /thinking - Toggle thinking display\n"
                "  /model    - Show current model\n"
                "  /context   - Show context window usage\n"
                "  /exit     - Exit the session"
            )
            await self.transport.send_event("query.info", {"message": help_text})
        else:
            await self.transport.send_event("query.info", {"message": f"Unknown command: {command}"})

    # ── Query execution ────────────────────────────────────────────────────

    async def _run_query(self, user_message: str) -> None:
        """Run a query and stream events to Ink via IPC."""
        self._tool_start_times.clear()

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

    async def _emit_event(self, event: QueryEvent) -> None:
        """Translate a QueryEvent into an IPC message and send it."""
        msg_type = QUERY_EVENT_MAP.get(event.type)
        if msg_type is None:
            logger.warning("IPC: unmapped event type: %s", event.type)
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

        await self.transport.send_event(msg_type.value, payload)

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

    # ── Context info ───────────────────────────────────────────────────────

    async def _send_context_info(self) -> None:
        """Send context window info to Ink."""
        from ..api.models import get_model_spec
        from ..compact.budget import (
            estimate_token_count,
            get_effective_context_window,
            get_auto_compact_threshold,
            calculate_token_warning_state,
        )

        store = Store()
        model = self.model or store.get("model", "deepseek-chat")
        spec = get_model_spec(model)
        messages = store.get("messages", [])
        token_usage = estimate_token_count(messages)
        effective = get_effective_context_window(model)
        threshold = get_auto_compact_threshold(model)
        warning_state = calculate_token_warning_state(token_usage, model)

        await self.transport.send_event(CoreToInk.CONTEXT_INFO.value, {
            "model": model,
            "contextWindow": spec.context_window,
            "maxOutputTokens": spec.max_output_tokens,
            "tokenUsage": token_usage,
            "effectiveWindow": effective,
            "autoCompactThreshold": threshold,
            "blockingAt": effective - 3000,
            "messageCount": len(messages),
            "warningState": ("blocking" if warning_state.is_at_blocking
                             else "auto_compact" if warning_state.is_above_auto_compact
                             else "warning" if warning_state.is_above_warning
                             else "ok"),
            "percentLeft": warning_state.percent_left,
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
