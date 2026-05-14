/**
 * App — Root Ink component for GG Bond Code.
 *
 * Manages:
 * - IPC message subscription
 * - REPL state (messages, input, permission dialogs)
 * - Theme context
 */

import React, { useState, useCallback, useEffect, useRef } from "react";
import { Box, Text, useInput, useApp } from "ink";
import { IPCTransport } from "./ipc/transport";
import { CoreToInk, InkToCore, Message } from "./ipc/protocol";
import { MessageList } from "./components/message-list";
import { InputBar } from "./components/input-bar";
import { PermissionDialog } from "./components/permission-dialog";
import { WelcomeScreen } from "./components/welcome-screen";

// ── Types ──────────────────────────────────────────────────────────────────────

interface DisplayMessage {
  id: string;
  type: "text" | "thinking" | "tool_use" | "tool_result" | "error" | "warning" | "system";
  content: string;
  toolName?: string;
  toolInput?: Record<string, unknown>;
  toolResult?: string;
  toolError?: boolean;
  elapsedMs?: number;
  metadata?: Record<string, unknown>;
}

interface PermissionRequest {
  requestId: string;
  toolName: string;
  params: Record<string, unknown>;
}

interface AppProps {
  transport: IPCTransport;
}

// ── Elapsed time hook ──────────────────────────────────────────────────────────

/** Returns elapsed seconds since `startMs`, updating every second while active. */
function useElapsedTime(startMs: number | null): number {
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (startMs === null) {
      setElapsed(0);
      if (timerRef.current !== null) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      return;
    }

    const update = () => setElapsed(Math.floor((Date.now() - startMs!) / 1000));
    update();
    timerRef.current = setInterval(update, 1000);

    return () => {
      if (timerRef.current !== null) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [startMs]);

  return elapsed;
}

// ── App Component ──────────────────────────────────────────────────────────────

export function App({ transport }: AppProps) {
  const { exit } = useApp();
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isQueryRunning, setIsQueryRunning] = useState(false);
  const [permissionRequest, setPermissionRequest] = useState<PermissionRequest | null>(null);
  const [showThinking, setShowThinking] = useState(false);
  const [model, setModel] = useState("unknown");
  const [cwd, setCwd] = useState("unknown");
  const [renderTick, setRenderTick] = useState(0);
  const [showWelcome, setShowWelcome] = useState(true);

  // Query timing: track when the current query started
  const [queryStartMs, setQueryStartMs] = useState<number | null>(null);
  const elapsedSec = useElapsedTime(queryStartMs);

  // Use ref for currentText to avoid stale closures in onMessage
  const currentTextRef = useRef("");
  const msgIdRef = useRef(0);
  const nextId = () => `msg-${++msgIdRef.current}`;

  // Batching: accumulate text deltas, flush at ~4fps (250ms) for smooth streaming
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dirtyRef = useRef(false);

  const scheduleFlush = useCallback(() => {
    dirtyRef.current = true;
    if (flushTimerRef.current === null) {
      flushTimerRef.current = setTimeout(() => {
        flushTimerRef.current = null;
        if (dirtyRef.current) {
          dirtyRef.current = false;
          setRenderTick((t) => t + 1);
        }
      }, 250); // ~4fps refresh rate — smooth, flicker-free
    }
  }, []);

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (flushTimerRef.current !== null) {
        clearTimeout(flushTimerRef.current);
      }
    };
  }, []);

  // Derive currentText from ref for rendering
  const currentText = currentTextRef.current;

  // Helper: finalize current accumulated text into messages
  const finalizeCurrentText = useCallback(() => {
    if (currentTextRef.current) {
      const text = currentTextRef.current;
      currentTextRef.current = "";
      setMessages((prev) => [...prev, { id: nextId(), type: "text", content: text }]);
    }
  }, []);

  // ── IPC Message Handler ───────────────────────────────────────────────────

  useEffect(() => {
    transport.onMessage((msg: Message) => {
      switch (msg.type) {
        case CoreToInk.SESSION_READY: {
          setModel((msg.payload as { model?: string }).model || "unknown");
          setCwd((msg.payload as { cwd?: string }).cwd || "unknown");
          break;
        }

        case CoreToInk.WELCOME: {
          setModel((msg.payload as { model?: string }).model || "unknown");
          setCwd((msg.payload as { cwd?: string }).cwd || "unknown");
          break;
        }

        case CoreToInk.QUERY_TEXT_DELTA: {
          const text = (msg.payload as { text?: string }).text || "";
          currentTextRef.current += text;
          scheduleFlush();
          break;
        }

        case CoreToInk.QUERY_THINKING_DELTA: {
          const text = (msg.payload as { text?: string }).text || "";
          if (showThinking) {
            currentTextRef.current += text;
            scheduleFlush();
          }
          break;
        }

        case CoreToInk.QUERY_TOOL_START: {
          finalizeCurrentText();
          scheduleFlush();
          break;
        }

        case CoreToInk.QUERY_TOOL_USE: {
          finalizeCurrentText();
          const payload = msg.payload as {
            toolUseId?: string;
            toolName?: string;
            toolInput?: Record<string, unknown>;
            toolPurpose?: string;
          };
          if (payload.toolPurpose) {
            setMessages((prev) => [...prev, { id: nextId(), type: "text", content: payload.toolPurpose! }]);
          }
          setMessages((prev) => [
            ...prev,
            {
              id: payload.toolUseId || nextId(),
              type: "tool_use",
              content: "",
              toolName: payload.toolName,
              toolInput: payload.toolInput,
            },
          ]);
          break;
        }

        case CoreToInk.QUERY_TOOL_RESULT: {
          const result = msg.payload as {
            toolUseId?: string;
            toolName?: string;
            toolResult?: string;
            toolError?: boolean;
            elapsedMs?: number;
          };
          setMessages((prev) => [
            ...prev,
            {
              id: result.toolUseId || nextId(),
              type: "tool_result",
              content: "",
              toolName: result.toolName,
              toolResult: result.toolResult,
              toolError: result.toolError,
              elapsedMs: result.elapsedMs,
            },
          ]);
          break;
        }

        case CoreToInk.QUERY_ERROR: {
          if (flushTimerRef.current !== null) {
            clearTimeout(flushTimerRef.current);
            flushTimerRef.current = null;
          }
          finalizeCurrentText();
          setMessages((prev) => [
            ...prev,
            { id: nextId(), type: "error", content: (msg.payload as { content?: string }).content || "" },
          ]);
          setIsQueryRunning(false);
          setQueryStartMs(null);
          break;
        }

        case CoreToInk.QUERY_WARNING: {
          setMessages((prev) => [
            ...prev,
            {
              id: nextId(),
              type: "warning",
              content: (msg.payload as { content?: string }).content || "",
              metadata: msg.payload.metadata as Record<string, unknown> | undefined,
            },
          ]);
          break;
        }

        case CoreToInk.QUERY_COMPLETE: {
          // Flush any pending batch immediately
          if (flushTimerRef.current !== null) {
            clearTimeout(flushTimerRef.current);
            flushTimerRef.current = null;
          }
          finalizeCurrentText();
          setRenderTick((t) => t + 1); // Immediate final render
          setIsQueryRunning(false);
          setQueryStartMs(null);
          break;
        }

        case CoreToInk.PERMISSION_REQUEST: {
          const req = msg.payload as {
            requestId?: string;
            toolName?: string;
            params?: Record<string, unknown>;
          };
          setPermissionRequest({
            requestId: req.requestId || "",
            toolName: req.toolName || "",
            params: req.params || {},
          });
          break;
        }

        case CoreToInk.CONTEXT_INFO: {
          const ctx = msg.payload as {
            model?: string;
            contextWindow?: number;
            maxOutputTokens?: number;
            tokenUsage?: number;
            effectiveWindow?: number;
            autoCompactThreshold?: number;
            blockingAt?: number;
            messageCount?: number;
            warningState?: string;
            percentLeft?: number;
          };
          const usage = ctx.tokenUsage ?? 0;
          const effective = ctx.effectiveWindow ?? 1;
          const usedPct = effective > 0 ? Math.round((usage / effective) * 100) : 0;
          const barLen = 30;
          const filled = effective > 0 ? Math.min(barLen, Math.round(barLen * usage / effective)) : 0;
          const bar = "█".repeat(filled) + "░".repeat(barLen - filled);
          const stateIcon = ctx.warningState === "blocking" ? "🔴" : ctx.warningState === "auto_compact" ? "🟡" : ctx.warningState === "warning" ? "🟡" : "🟢";
          const stateLabel = ctx.warningState === "blocking" ? "Blocking" : ctx.warningState === "auto_compact" ? "Auto-Compact" : ctx.warningState === "warning" ? "Warning" : "OK";
          const fmt = (n: number) => n.toLocaleString();

          const lines = [
            `Model:           ${ctx.model ?? "unknown"}`,
            `Context Window:   ${fmt(ctx.contextWindow ?? 0)} tokens`,
            `Max Output:       ${fmt(ctx.maxOutputTokens ?? 0)} tokens`,
            `Effective Window: ${fmt(effective)} tokens`,
            `Auto-Compact at:  ${fmt(ctx.autoCompactThreshold ?? 0)} tokens (${ctx.autoCompactThreshold && effective ? Math.round(ctx.autoCompactThreshold / effective * 100) : 0}% of effective)`,
            `Blocking at:      ${fmt(ctx.blockingAt ?? 0)} tokens`,
            ``,
            `Token Usage:      ${fmt(usage)} / ${fmt(effective)} (${usedPct}%)`,
            `                  ${bar} ${usedPct}%`,
            `Messages:         ${ctx.messageCount ?? 0}`,
            ``,
            `Warning State:    ${stateIcon} ${stateLabel}`,
            `Percent Left:     ${ctx.percentLeft ?? 100}%`,
          ];

          setMessages((prev) => [
            ...prev,
            { id: nextId(), type: "info", content: lines.join("\n") },
          ]);
          break;
        }

        case CoreToInk.QUERY_INFO: {
          const infoMsg = (msg.payload as { message?: string }).message || "";
          if (infoMsg) {
            setMessages((prev) => [
              ...prev,
              { id: nextId(), type: "info", content: infoMsg },
            ]);
          }
          break;
        }

        case CoreToInk.QUERY_CLEARED: {
          setMessages([]);
          currentTextRef.current = "";
          break;
        }

        case CoreToInk.COMPACT_STARTED: {
          setMessages((prev) => [
            ...prev,
            { id: nextId(), type: "info", content: "Compacting conversation..." },
          ]);
          break;
        }

        case CoreToInk.COMPACT_COMPLETE: {
          const reason = (msg.payload as { reason?: string }).reason || "done";
          setMessages((prev) => [
            ...prev,
            { id: nextId(), type: "info", content: `Compact complete: ${reason}` },
          ]);
          break;
        }

        case CoreToInk.STATE_UPDATE: {
          // TODO: handle state updates
          break;
        }

        case CoreToInk.SESSION_SHUTDOWN: {
          transport.close();
          exit();
          break;
        }

        case CoreToInk.PING: {
          transport.sendEvent(InkToCore.PONG, { timestamp: Date.now() });
          break;
        }
      }
    });
  }, [transport, showThinking, finalizeCurrentText, scheduleFlush]);

  // ── User Input ────────────────────────────────────────────────────────────

  const handleSubmit = useCallback(
    (text: string) => {
      if (text.startsWith("/")) {
        // Show the command in message list, then send to backend
        setMessages((prev) => [
          ...prev,
          { id: nextId(), type: "command", content: text },
        ]);
        transport.sendEvent(InkToCore.USER_COMMAND, { command: text });
        if (text.toLowerCase() === "/exit" || text.toLowerCase() === "/quit") {
          exit();
        }
      } else if (text.trim()) {
        setIsQueryRunning(true);
        setQueryStartMs(Date.now());
        setShowWelcome(false);
        // Show user's question in message list
        setMessages((prev) => [
          ...prev,
          { id: nextId(), type: "system", content: text },
        ]);
        transport.sendEvent(InkToCore.USER_MESSAGE, { text });
      }
    },
    [transport, exit]
  );

  const handlePermissionResponse = useCallback(
    (decision: "allow" | "deny" | "always_allow") => {
      if (permissionRequest) {
        transport.sendEvent(InkToCore.PERMISSION_RESPONSE, {
          requestId: permissionRequest.requestId,
          toolName: permissionRequest.toolName,
          params: permissionRequest.params,
          decision,
          wildcard: decision === "always_allow",
        });
        setPermissionRequest(null);
      }
    },
    [transport, permissionRequest]
  );

  // ── Format elapsed time ──────────────────────────────────────────────────

  const formatElapsed = (sec: number): string => {
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}m${s}s`;
  };

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <Box flexDirection="column" height="100%">
      {showWelcome && <WelcomeScreen model={model} cwd={cwd} />}
      <MessageList messages={messages} currentText={currentText} />
      {/* Thinking indicator with live elapsed time */}
      {isQueryRunning && queryStartMs !== null && (
        <Box marginTop={0} marginLeft={1}>
          <Text italic color="yellow">*thinking </Text>
          <Text dimColor>{formatElapsed(elapsedSec)}</Text>
          <Text italic color="yellow">...</Text>
        </Box>
      )}
      {permissionRequest ? (
        <PermissionDialog
          toolName={permissionRequest.toolName}
          params={permissionRequest.params}
          onResponse={handlePermissionResponse}
        />
      ) : (
        <InputBar
          inputValue={inputValue}
          setInputValue={setInputValue}
          onSubmit={handleSubmit}
          disabled={isQueryRunning}
          model={model}
        />
      )}
    </Box>
  );
}