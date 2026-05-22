/**
 * App — Root Ink component for NextCode.
 *
 * Manages:
 * - IPC message subscription
 * - REPL state (messages, input, permission dialogs)
 * - Theme context
 */

import React, { useState, useCallback, useEffect, useRef } from "react";
import { Box, Text, useInput, useApp, useStdout } from "ink";

/** ANSI escape: erase current line and move cursor up one line. */
const ERASE_LINE = "\x1b[2K";
const CURSOR_UP = "\x1b[1A";
const CURSOR_LEFT = "\x1b[G";

/** Erase N terminal lines (current line + N-1 above). */
function eraseLines(count: number): string {
  let clear = "";
  for (let i = 0; i < count; i++) {
    clear += ERASE_LINE + (i < count - 1 ? CURSOR_UP : "");
  }
  if (count) clear += CURSOR_LEFT;
  return clear;
}
import { IPCTransport } from "./ipc/transport";
import { CoreToInk, InkToCore, Message, CommandInfo } from "./ipc/protocol";
import { MessageList } from "./components/message-list";
import { InputBar } from "./components/input-bar";
import { PermissionDialog } from "./components/permission-dialog";
import { WelcomeScreen } from "./components/welcome-screen";

// ── Types ──────────────────────────────────────────────────────────────────────

interface DisplayMessage {
  id: string;
  type: "text" | "thinking" | "tool_use" | "tool_result" | "error" | "warning" | "system" | "info" | "command" | "agent_start" | "agent_tool_use" | "agent_tool_result" | "agent_result" | "queued" | "task_notification";
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
      // No need to setElapsed(0) — causes an extra re-render.
      // The component using this already hides itself when startMs is null.
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
  const [inputState, setInputState] = useState({ value: "", cursor: 0 });
  const [isQueryRunning, setIsQueryRunning] = useState(false);
  const [permissionRequest, setPermissionRequest] = useState<PermissionRequest | null>(null);
  const [showThinking, setShowThinking] = useState(false);
  const [model, setModel] = useState("unknown");
  const [cwd, setCwd] = useState("unknown");
  const [commands, setCommands] = useState<CommandInfo[]>([]);
  const [renderTick, setRenderTick] = useState(0);
  const [showWelcome, setShowWelcome] = useState(true);
  const { stdout: appStdout } = useStdout();
  const [columns, setColumns] = useState(appStdout?.columns || 120);

  // Background task tracking: count of running bash/agent tasks
  const [bgTaskCount, setBgTaskCount] = useState({ bash: 0, agent: 0 });

  // Real-time task output: task_id -> output lines (for running tasks)
  // Each entry stores the last N lines received for that task
  const [taskOutputs, setTaskOutputs] = useState<Map<string, string>>(new Map());
  // Ref for batching task output updates — avoids re-rendering on every output chunk
  const taskOutputBatchRef = useRef<Map<string, string>>(new Map());
  const taskOutputFlushRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Task state: full info for each task (running + completed)
  // Used for task panel and retain/stop interactions
  interface TaskInfo {
    task_id: string;
    task_type: string;
    status: "pending" | "running" | "completed" | "failed" | "killed";
    description: string;
    retain: boolean;
    started_at: number;
    result?: string;
  }
  const [tasks, setTasks] = useState<Map<string, TaskInfo>>(new Map());

  // Current running tool info — shown in the status bar during execution
  const [currentTool, setCurrentTool] = useState<string | null>(null);

  // Pending task notifications — buffered while a query is running,
  // flushed to message list after the current query completes.
  const pendingNotificationsRef = useRef<DisplayMessage[]>([]);

  // Query timing: track when the current query started
  const [queryStartMs, setQueryStartMs] = useState<number | null>(null);
  const elapsedSec = useElapsedTime(queryStartMs);

  // Use ref for currentText to avoid stale closures in onMessage
  const currentTextRef = useRef("");
  // Track last displayed toolPurpose to avoid duplicates from parallel tool calls
  const lastPurposeRef = useRef("");
  // Queue of user messages submitted while a query is running.
  // Shown above the input bar as pending tasks. When the current response
  // finishes, the first queued message is sent as the next query.
  // Using ref + state pair: ref for synchronous access in onMessage,
  // state for triggering re-render to show the pending list.
  const pendingQuestionsRef = useRef<string[]>([]);
  const [pendingQuestions, setPendingQuestions] = useState<string[]>([]);
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

  // Cleanup timers on unmount
  useEffect(() => {
    return () => {
      if (flushTimerRef.current !== null) {
        clearTimeout(flushTimerRef.current);
      }
      if (taskOutputFlushRef.current !== null) {
        clearTimeout(taskOutputFlushRef.current);
      }
    };
  }, []);

  // Handle terminal resize — clear dynamic area to prevent misaligned output
  // when the window gets narrower (Ink's log-update previousLineCount may be
  // too low after resize, causing old content to remain on screen).
  useEffect(() => {
    if (!appStdout) return;
    const handleResize = () => {
      appStdout.write(eraseLines(20));
      setColumns(appStdout.columns || 120);
    };
    appStdout.on("resize", handleResize);
    return () => { appStdout.off("resize", handleResize); };
  }, [appStdout]);

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

  // Helper: finalize accumulated sub-agent text into messages (no-op now — not used)
  const finalizeAgentText = useCallback(() => {}, []);

  // ── IPC Message Handler ───────────────────────────────────────────────────

  useEffect(() => {
    transport.onMessage((msg: Message) => {
      switch (msg.type) {
        case CoreToInk.SESSION_READY: {
          setModel((msg.payload as { model?: string }).model || "unknown");
          setCwd((msg.payload as { cwd?: string }).cwd || "unknown");
          break;
        }

        case CoreToInk.COMMANDS_UPDATE: {
          const cmds = (msg.payload as { commands?: CommandInfo[] }).commands || [];
          setCommands(cmds);
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
          finalizeAgentText();
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
          // Track the current running tool for the status bar
          if (payload.toolName) {
            setCurrentTool(payload.toolName);
          }
          // Show toolPurpose text once — skip if it's the same as the last one
          // (parallel tool calls share the same purpose, avoid duplication)
          if (payload.toolPurpose && payload.toolPurpose !== lastPurposeRef.current) {
            lastPurposeRef.current = payload.toolPurpose;
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
          setCurrentTool(null);
          const result = msg.payload as {
            toolUseId?: string;
            toolName?: string;
            toolResult?: string;
            toolError?: boolean;
            elapsedMs?: number;
            toolMetadata?: Record<string, unknown>;
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
              metadata: result.toolMetadata,
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
          setCurrentTool(null);
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

        case CoreToInk.AGENT_START: {
          finalizeCurrentText();
          const agentMeta = msg.payload as {
            agent_id?: string;
            agent_type?: string;
            description?: string;
            prompt?: string;
          };
          setMessages((prev) => [
            ...prev,
            {
              id: nextId(),
              type: "agent_start",
              content: agentMeta.description || agentMeta.agent_type || "Agent",
              metadata: { ...msg.payload, _startMs: Date.now() } as Record<string, unknown>,
            },
          ]);
          break;
        }

        case CoreToInk.AGENT_TEXT_DELTA: {
          // No longer streaming sub-agent text — events not forwarded from backend
          break;
        }

        case CoreToInk.AGENT_TOOL_USE: {
          // No longer showing sub-agent tool calls — events not forwarded from backend
          break;
        }

        case CoreToInk.AGENT_TOOL_RESULT: {
          // No longer showing sub-agent tool results — events not forwarded from backend
          break;
        }

        case CoreToInk.AGENT_PROGRESS: {
          // No longer updating agent_start message — it's in <Static> now,
          // so mutations after render won't be reflected. Tool count is shown
          // in the agent_result message instead.
          break;
        }

        case CoreToInk.AGENT_RESULT: {
          // Add agent_result message with final info — no longer mutating
          // the agent_start message since it's rendered in <Static>.
          const resultPayload = msg.payload as { agent_id?: string; elapsed?: string; tool_use_count?: number; agent_type?: string };
          setMessages((prev) => [
            ...prev,
            {
              id: nextId(),
              type: "agent_result",
              content: "",
              metadata: {
                agent_id: resultPayload.agent_id,
                agent_type: resultPayload.agent_type,
                _elapsed: resultPayload.elapsed || "",
                tool_use_count: resultPayload.tool_use_count || 0,
              },
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
          // Flush any pending task output batch
          if (taskOutputFlushRef.current !== null) {
            clearTimeout(taskOutputFlushRef.current);
            taskOutputFlushRef.current = null;
            const batch = taskOutputBatchRef.current;
            taskOutputBatchRef.current = new Map();
            setTaskOutputs((prev) => {
              const next = new Map(prev);
              for (const [id, output] of batch) {
                next.set(id, output);
              }
              return next;
            });
          }
          finalizeCurrentText();
          setCurrentTool(null);
          setRenderTick((t) => t + 1); // Immediate final render
          // Flush any pending task notifications after the query output
          const pendingNotifs = pendingNotificationsRef.current;
          if (pendingNotifs.length > 0) {
            pendingNotificationsRef.current = [];
            setMessages((prev) => [...prev, ...pendingNotifs]);
          }
          // Check pending queue for the next question
          const pending = pendingQuestionsRef.current;
          if (pending.length > 0) {
            const next = pending.shift()!;
            setPendingQuestions([...pending]);
            // Show the question in message list after the answer
            setMessages((prev) => [
              ...prev,
              { id: nextId(), type: "system", content: next },
            ]);
            setIsQueryRunning(true);
            setQueryStartMs(Date.now());
            transport.sendEvent(InkToCore.USER_MESSAGE, { text: next });
          } else {
            setIsQueryRunning(false);
            setQueryStartMs(null);
          }
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
          setShowWelcome(true);
          break;
        }

        case CoreToInk.QUERY_QUEUED: {
          // Backend confirmed the message was queued — already shown by handleSubmit
          break;
        }

        case CoreToInk.QUERY_DEQUEUE: {
          // Backend confirms dequeued — no action needed, frontend already
          // sent the message directly on QUERY_COMPLETE.
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

        case CoreToInk.TASK_COUNT: {
          const tc = msg.payload as { bash?: number; agent?: number };
          setBgTaskCount({ bash: tc.bash || 0, agent: tc.agent || 0 });
          break;
        }

        case CoreToInk.TASK_OUTPUT: {
          // Real-time output streaming for running tasks
          // Batch updates: accumulate in ref, flush to state at ~2fps
          const to = msg.payload as { task_id?: string; output?: string; is_running?: boolean };
          if (to.task_id && to.output !== undefined) {
            taskOutputBatchRef.current.set(to.task_id!, to.output || "");
            if (taskOutputFlushRef.current === null) {
              taskOutputFlushRef.current = setTimeout(() => {
                taskOutputFlushRef.current = null;
                const batch = taskOutputBatchRef.current;
                taskOutputBatchRef.current = new Map();
                setTaskOutputs((prev) => {
                  const next = new Map(prev);
                  for (const [id, output] of batch) {
                    next.set(id, output);
                  }
                  return next;
                });
              }, 500);
            }
          }
          break;
        }

        case CoreToInk.TASK_STARTED: {
          const ts = msg.payload as { task_id?: string; task_type?: string; description?: string };
          const typeLabel = ts.task_type === "local_agent" ? "Agent" : "Shell";
          const taskId = ts.task_id || "";
          setMessages((prev) => [
            ...prev,
            {
              id: nextId(),
              type: "task_notification",
              content: `⏳ Background ${typeLabel} started: ${taskId}`,
              metadata: { isStarted: true, description: ts.description || "" },
            },
          ]);
          // Update task state
          setTasks((prev) => {
            const next = new Map(prev);
            next.set(taskId, {
              task_id: taskId,
              task_type: ts.task_type || "local_bash",
              status: "running",
              description: ts.description || "",
              retain: false,
              started_at: Date.now(),
            });
            return next;
          });
          break;
        }

        case CoreToInk.TASK_COMPLETED:
        case CoreToInk.TASK_FAILED: {
          const tf = msg.payload as { task_id?: string; task_type?: string; status?: string; result?: string; description?: string };
          const isFailed = msg.type === CoreToInk.TASK_FAILED;
          const icon = isFailed ? "✗" : "✓";
          const typeLabel = tf.task_type === "local_agent" ? "Agent" : "Shell";
          const taskId = tf.task_id || "";
          // Use the streamed output if available, otherwise use result
          const streamedOutput = taskOutputs.get(taskId);
          const outputToShow = streamedOutput || tf.result || "";
          const notification: DisplayMessage = {
            id: nextId(),
            type: "task_notification",
            content: `${icon} Background ${typeLabel} ${isFailed ? "failed" : "completed"}`,
            metadata: {
              result: outputToShow,
              isFailed,
              description: tf.description || "",
            },
          };
          // Clean up streamed output for this task
          taskOutputBatchRef.current.delete(taskId);
          setTaskOutputs((prev) => {
            const next = new Map(prev);
            next.delete(taskId);
            return next;
          });
          // Update task state to completed/failed
          setTasks((prev) => {
            const next = new Map(prev);
            const existing = next.get(taskId);
            if (existing) {
              next.set(taskId, {
                ...existing,
                status: isFailed ? "failed" : "completed",
                result: outputToShow,
              });
            } else {
              next.set(taskId, {
                task_id: taskId,
                task_type: tf.task_type || "local_bash",
                status: isFailed ? "failed" : "completed",
                description: tf.description || "",
                retain: false,
                started_at: Date.now(),
                result: outputToShow,
              });
            }
            return next;
          });
          if (isQueryRunning) {
            pendingNotificationsRef.current.push(notification);
          } else {
            setMessages((prev) => [...prev, notification]);
          }
          break;
        }

        case CoreToInk.TASK_STALLED: {
          // Task may be waiting for interactive input (e.g., apt/yum confirmation)
          const stall = msg.payload as { task_id?: string; prompt_type?: string; message?: string };
          const taskId = stall.task_id || "";
          const typeLabel = stall.prompt_type || "interactive prompt";
          const notif: DisplayMessage = {
            id: nextId(),
            type: "task_notification",
            content: `⚠️ Task ${taskId} may be stalled: ${typeLabel}`,
            metadata: {
              isStalled: true,
              promptType: stall.prompt_type || "",
              message: stall.message || "",
            },
          };
          // Mark task as stalled in task state
          setTasks((prev) => {
            const next = new Map(prev);
            const existing = next.get(taskId);
            if (existing) {
              next.set(taskId, { ...existing });
            }
            return next;
          });
          if (isQueryRunning) {
            pendingNotificationsRef.current.push(notif);
          } else {
            setMessages((prev) => [...prev, notif]);
          }
          break;
        }
      }
    });
  }, [transport, showThinking, finalizeCurrentText, scheduleFlush]);

  // ── User Input ────────────────────────────────────────────────────────────

  // Double-ESC interrupt: press Escape twice within 500ms to cancel the
  // current query. Uses a ref to track the last ESC timestamp.
  const lastEscRef = useRef(0);

  useInput((input, key) => {
    if (key.escape && isQueryRunning) {
      const now = Date.now();
      if (now - lastEscRef.current < 500) {
        // Double ESC — interrupt current query
        lastEscRef.current = 0;
        transport.sendEvent(InkToCore.USER_INTERRUPT, {});
        setMessages((prev) => [
          ...prev,
          { id: nextId(), type: "info", content: "⏹ Interrupted" },
        ]);
        setIsQueryRunning(false);
        setQueryStartMs(null);
        pendingQuestionsRef.current = [];
        setPendingQuestions([]);
      } else {
        lastEscRef.current = now;
      }
    }
  });

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
        setShowWelcome(false);
        if (isQueryRunning) {
          // Query is running — add to pending list shown above input bar.
          // Will be sent as the next query after the current response finishes.
          pendingQuestionsRef.current.push(text);
          setPendingQuestions([...pendingQuestionsRef.current]);
        } else {
          setIsQueryRunning(true);
          setQueryStartMs(Date.now());
          lastPurposeRef.current = "";
          // Show user's question in message list
          setMessages((prev) => [
            ...prev,
            { id: nextId(), type: "system", content: text },
          ]);
          transport.sendEvent(InkToCore.USER_MESSAGE, { text });
        }
      }
    },
    [transport, exit, isQueryRunning]
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

  // ── Task control handlers ────────────────────────────────────────────────

  const handleTaskStop = useCallback(
    (taskId: string) => {
      transport.sendEvent(InkToCore.TASK_STOP, { task_id: taskId });
      // Update local state
      setTasks((prev) => {
        const next = new Map(prev);
        const existing = next.get(taskId);
        if (existing) {
          next.set(taskId, { ...existing, status: "killed" });
        }
        return next;
      });
    },
    [transport]
  );

  const handleTaskRetain = useCallback(
    (taskId: string, retain: boolean) => {
      transport.sendEvent(InkToCore.TASK_RETAIN, { task_id: taskId, retain });
      // Update local state
      setTasks((prev) => {
        const next = new Map(prev);
        const existing = next.get(taskId);
        if (existing) {
          next.set(taskId, { ...existing, retain });
        }
        return next;
      });
    },
    [transport]
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
      {/* Status bar: shows current activity with elapsed time */}
      {isQueryRunning && queryStartMs !== null && (
        <Box marginTop={0} marginLeft={1}>
          {currentTool ? (
            <>
              <Text italic color="yellow">*Execute {currentTool} </Text>
              <Text dimColor>{formatElapsed(elapsedSec)}</Text>
            </>
          ) : (
            <>
              <Text italic color="yellow">*thinking </Text>
              <Text dimColor>{formatElapsed(elapsedSec)}</Text>
              <Text italic color="yellow">...</Text>
            </>
          )}
          {(bgTaskCount.bash > 0 || bgTaskCount.agent > 0) && (
            <Text dimColor> | bg: {[
              bgTaskCount.bash > 0 && `${bgTaskCount.bash} shell`,
              bgTaskCount.agent > 0 && `${bgTaskCount.agent} agent`,
            ].filter(Boolean).join(", ")}</Text>
          )}
        </Box>
      )}
      {/* Show background task output while tasks are running */}
      {taskOutputs.size > 0 && (
        <Box flexDirection="column" marginTop={0} marginLeft={1}>
          {Array.from(taskOutputs.entries()).map(([taskId, output]) => (
            <Box key={taskId} flexDirection="column">
              <Text dimColor>{`⎿  ${taskId}:`}</Text>
              <Box marginLeft={4}>
                {output.split("\n").slice(-5).map((line, i) => (
                  <Text key={i} dimColor>{line}</Text>
                ))}
              </Box>
            </Box>
          ))}
        </Box>
      )}
      {/* Show background task count even when not thinking */}
      {!isQueryRunning && (bgTaskCount.bash > 0 || bgTaskCount.agent > 0) && (
        <Box marginTop={0} marginLeft={1}>
          <Text dimColor>bg: {[
            bgTaskCount.bash > 0 && `${bgTaskCount.bash} shell`,
            bgTaskCount.agent > 0 && `${bgTaskCount.agent} agent`,
          ].filter(Boolean).join(", ")}</Text>
        </Box>
      )}
      {/* Pending questions shown above input bar while a query is running */}
      {pendingQuestions.length > 0 && (
        <Box flexDirection="column">
          {pendingQuestions.map((q, i) => (
            <Box key={i} marginTop={1} marginBottom={0}>
              <Text dimColor color="yellow">⏳ </Text>
              <Text backgroundColor="gray" color="white" bold>{"> "}</Text>
              <Text backgroundColor="gray" color="white">{" " + q + " "}</Text>
            </Box>
          ))}
        </Box>
      )}
      {/* Permission dialog with inline input — replaces InputBar when active */}
      {permissionRequest ? (
        <PermissionDialog
          toolName={permissionRequest.toolName}
          params={permissionRequest.params}
          onResponse={handlePermissionResponse}
        />
      ) : (
        <InputBar
          inputState={inputState}
          setInputState={setInputState}
          onSubmit={handleSubmit}
          disabled={false}
          isQueryRunning={isQueryRunning}
          model={model}
          commands={commands}
          columns={columns}
        />
      )}
    </Box>
  );
}