/**
 * App — Root Ink component for NextCode.
 *
 * Manages:
 * - IPC message subscription
 * - REPL state (messages, input, permission dialogs)
 * - Theme context
 */

import React, { useState, useCallback, useEffect, useRef } from "react";
import { Box, Text, useInput, useApp } from "ink";
import { IPCTransport } from "./ipc/transport";
import { CoreToInk, InkToCore, Message, CommandInfo } from "./ipc/protocol";
import { MessageList } from "./components/message-list";
import { InputBar } from "./components/input-bar";
import { PermissionDialog } from "./components/permission-dialog";
import { WelcomeScreen } from "./components/welcome-screen";

// ── Types ──────────────────────────────────────────────────────────────────────

interface AgentEntry {
  agent_id: string;
  agent_type: string;
  description: string;
  status: "running" | "done" | "failed";
  tool_use_count: number;
  elapsed: string;
  is_background: boolean;
}

interface DisplayMessage {
  id: string;
  type: "text" | "thinking" | "tool_use" | "tool_result" | "error" | "warning" | "system" | "info" | "command" | "agent_group" | "agent_result" | "queued" | "task_notification";
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

// ── Progress bar helper ────────────────────────────────────────────────────────

function makeBar(done: number, total: number, width = 10): string {
  if (total <= 0) return "";
  const filled = Math.round((done / total) * width);
  return "█".repeat(filled) + "░".repeat(width - filled);
}

// ── App Component ──────────────────────────────────────────────────────────────

export function App({ transport }: AppProps) {
  const { exit } = useApp();
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [inputState, setInputState] = useState({ value: "", cursor: 0 });
  const [isQueryRunning, setIsQueryRunning] = useState(false);
  const [permissionRequest, setPermissionRequest] = useState<PermissionRequest | null>(null);
  const [model, setModel] = useState("unknown");
  const [cwd, setCwd] = useState("unknown");
  const [permissionMode, setPermissionMode] = useState<string>("default");
  const [commands, setCommands] = useState<CommandInfo[]>([]);
  const [renderTick, setRenderTick] = useState(0);
  const [showWelcome, setShowWelcome] = useState(true);
  const [isDisconnected, setIsDisconnected] = useState(false);

  // Context bar state: shows compact token usage progress bar above input
  const [contextInfo, setContextInfo] = useState<{
    tokenUsage: number;
    effectiveWindow: number;
    warningState: string;
  } | null>(null);
  const [showContextBar, setShowContextBar] = useState(false);

  // Background task tracking: count of running + done bash/agent tasks
  const [bgTaskCount, setBgTaskCount] = useState({ bash: 0, agent: 0, bash_done: 0, agent_done: 0 });

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
  // Separate ref for thinking text — accumulated during thinking phase
  const thinkingTextRef = useRef("");
  // Track last displayed toolPurpose to avoid duplicates from parallel tool calls
  const lastPurposeRef = useRef("");
  // Track the latest Agent tool purpose for use as agent_group title
  const agentGroupTitleRef = useRef("");  // Queue of user messages submitted while a query is running.
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

  // Derive currentText from ref for rendering
  const currentText = currentTextRef.current;
  const thinkingText = thinkingTextRef.current;

  // Helper: clear thinking text buffer (status bar already displays it live)
  const finalizeThinkingText = useCallback(() => {
    thinkingTextRef.current = "";
  }, []);

  // Helper: finalize current accumulated text into messages
  const finalizeCurrentText = useCallback(() => {
    // Finalize any pending thinking first
    finalizeThinkingText();
    if (currentTextRef.current) {
      const text = currentTextRef.current;
      currentTextRef.current = "";
      setMessages((prev) => [...prev, { id: nextId(), type: "text", content: text }]);
    }
  }, [finalizeThinkingText]);

  // Helper: finalize accumulated sub-agent text into messages (no-op now — not used)
  const finalizeAgentText = useCallback(() => {}, []);

  // ── IPC Message Handler ───────────────────────────────────────────────────

  // Register disconnect handler — show error when backend disconnects
  useEffect(() => {
    transport.onDisconnect(() => {
      setIsDisconnected(true);
      setIsQueryRunning(false);
      setQueryStartMs(null);
    });
  }, [transport]);

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
          // If thinking was being accumulated, finalize it before starting text
          if (thinkingTextRef.current) {
            finalizeThinkingText();
          }
          const text = (msg.payload as { text?: string }).text || "";
          currentTextRef.current += text;
          scheduleFlush();
          break;
        }

        case CoreToInk.QUERY_THINKING_DELTA: {
          const text = (msg.payload as { text?: string }).text || "";
          // Accumulate thinking text for status bar display (not shown in message list)
          thinkingTextRef.current += text;
          scheduleFlush();
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
          // Agent tool: extract description param as group title
          // Only use as title if all parallel agents share the same description.
          // If different descriptions come in, clear title to fallback to "N Agents".
          if (payload.toolName === "Agent") {
            const desc = (payload.toolInput as Record<string, unknown>)?.description as string || "";
            if (desc) {
              if (!agentGroupTitleRef.current) {
                agentGroupTitleRef.current = desc;
              } else if (agentGroupTitleRef.current !== desc) {
                agentGroupTitleRef.current = "";
              }
            }
          }
          // Show toolPurpose text once - skip if same as last
          // (parallel tool calls share the same purpose, avoid duplication)
          if (payload.toolPurpose && payload.toolPurpose !== lastPurposeRef.current) {
            lastPurposeRef.current = payload.toolPurpose;
            if (payload.toolName !== "Agent") {
              setMessages((prev) => [...prev, { id: nextId(), type: "text", content: payload.toolPurpose! }]);
            }
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
            is_background?: boolean;
          };
          const newAgent: AgentEntry = {
            agent_id: agentMeta.agent_id || "",
            agent_type: agentMeta.agent_type || "Agent",
            description: agentMeta.description || agentMeta.agent_type || "Agent",
            status: "running",
            tool_use_count: 0,
            elapsed: "",
            is_background: agentMeta.is_background || false,
          };
          setMessages((prev) => {
            // Find the last agent_group message that still has running agents
            const lastOpenGroupIdx = prev.findLastIndex(
              (m) => m.type === "agent_group" && !(m.metadata?._allDone as boolean)
            );
            if (lastOpenGroupIdx >= 0) {
              // Append this agent to the existing group
              return prev.map((m, i) => {
                if (i === lastOpenGroupIdx) {
                  const existingAgents = (m.metadata?._agents as AgentEntry[]) || [];
                  return {
                    ...m,
                    metadata: {
                      ...m.metadata,
                      _agents: [...existingAgents, newAgent],
                    },
                  };
                }
                return m;
              });
            }
            // No open group — create a new one
            return [
              ...prev,
              {
                id: nextId(),
                type: "agent_group" as const,
                content: "",
                metadata: {
                  _startMs: Date.now(),
                  _title: agentGroupTitleRef.current || "",
                  _agents: [newAgent],
                  _allDone: false,
                } as Record<string, unknown>,
              },
            ];
          });
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
          const progress = msg.payload as {
            agent_id?: string;
            tool_use_count?: number;
          };
          if (progress.agent_id) {
            setMessages((prev) => prev.map((m) => {
              if (m.type === "agent_group") {
                const agents = (m.metadata?._agents as AgentEntry[]) || [];
                const updated = agents.map((a) =>
                  a.agent_id === progress.agent_id
                    ? { ...a, tool_use_count: progress.tool_use_count || 0 }
                    : a
                );
                if (updated !== agents) {
                  return { ...m, metadata: { ...m.metadata, _agents: updated } };
                }
              }
              return m;
            }));
          }
          break;
        }

        case CoreToInk.AGENT_RESULT: {
          const resultPayload = msg.payload as { agent_id?: string; elapsed?: string; tool_use_count?: number; agent_type?: string };
          if (resultPayload.agent_id) {
            setMessages((prev) => prev.map((m) => {
              if (m.type === "agent_group") {
                const agents = (m.metadata?._agents as AgentEntry[]) || [];
                const hasAgent = agents.some((a) => a.agent_id === resultPayload.agent_id);
                if (!hasAgent) return m;
                const updatedAgents = agents.map((a) =>
                  a.agent_id === resultPayload.agent_id
                    ? {
                        ...a,
                        status: "done" as const,
                        elapsed: resultPayload.elapsed || "",
                        tool_use_count: resultPayload.tool_use_count || a.tool_use_count,
                      }
                    : a
                );
                const allDone = updatedAgents.every((a) => a.status === "done" || a.status === "failed");
                return {
                  ...m,
                  metadata: {
                    ...m.metadata,
                    _agents: updatedAgents,
                    _allDone: allDone,
                    _finalElapsed: allDone ? resultPayload.elapsed || "" : "",
                  },
                };
              }
              return m;
            }));
          }
          break;
        }

        case CoreToInk.QUERY_COMPLETE: {
          const transitionReason = (msg.payload as { transitionReason?: string }).transitionReason || "done";
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
          // On cancel, mark all running agents as cancelled so old groups
          // don't absorb new agents from the next query
          if (transitionReason === "cancelled") {
            setMessages((prev) => prev.map((m) => {
              if (m.type === "agent_group" && !(m.metadata?._allDone as boolean)) {
                const agents = (m.metadata?._agents as AgentEntry[]) || [];
                const updatedAgents = agents.map((a) =>
                  a.status === "running" ? { ...a, status: "done" as const, elapsed: "cancelled" } : a
                );
                return {
                  ...m,
                  metadata: {
                    ...m.metadata,
                    _agents: updatedAgents,
                    _allDone: true,
                  },
                };
              }
              return m;
            }));
            agentGroupTitleRef.current = "";
          }
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
            auto?: boolean;
          };
          const usage = ctx.tokenUsage ?? 0;
          const effective = ctx.effectiveWindow ?? 1;

          // Always update context bar state
          setContextInfo({
            tokenUsage: usage,
            effectiveWindow: effective,
            warningState: ctx.warningState ?? "ok",
          });

          if (ctx.auto) {
            // Auto-update from backend (after query completion): only refresh bar, no message
            // If context bar is not yet shown, show it on first auto-update
            if (!showContextBar) {
              setShowContextBar(true);
            }
          } else {
            // Manual /context command: show detailed info in message list + toggle bar
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

            // Toggle context bar visibility
            setShowContextBar((prev) => !prev);
          }
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

        case CoreToInk.PERMISSION_MODE_UPDATE: {
          const modePayload = msg.payload as { mode?: string };
          setPermissionMode(modePayload.mode || "default");
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
          const tc = msg.payload as { bash?: number; agent?: number; bash_done?: number; agent_done?: number };
          const bash = tc.bash || 0;
          const agent = tc.agent || 0;
          const bash_done = tc.bash_done || 0;
          const agent_done = tc.agent_done || 0;
          setBgTaskCount((prev) => {
            if (prev.bash === bash && prev.agent === agent && prev.bash_done === bash_done && prev.agent_done === agent_done) {
              return prev;
            }
            return { bash, agent, bash_done, agent_done };
          });
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
          const taskId = ts.task_id || "";
          // local_agent tasks are shown via AGENT_START -> agent_group, skip task_notification
          if (ts.task_type !== "local_agent") {
            setMessages((prev) => [
              ...prev,
              {
                id: nextId(),
                type: "task_notification",
                content: `⏳ Background Shell started: ${taskId}`,
                metadata: { isStarted: true, description: ts.description || "" },
              },
            ]);
          }
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
          // local_agent tasks shown via agent_group
          const taskId = tf.task_id || "";
          // Use the streamed output if available, otherwise use result
          const streamedOutput = taskOutputs.get(taskId);
          const outputToShow = streamedOutput || tf.result || "";
          const notification: DisplayMessage = {
            id: nextId(),
            type: "task_notification",
            content: `${icon} Background Shell ${isFailed ? "failed" : "completed"}`,
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
          // local_agent tasks shown via agent_group, skip notification
          if (tf.task_type !== "local_agent") {
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
          // Always show task notifications immediately
          setMessages((prev) => [...prev, notif]);
          break;
        }
      }
    });
  }, [transport, finalizeCurrentText, scheduleFlush]);

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
        // Show user's question in message list
        setMessages((prev) => [
          ...prev,
          { id: nextId(), type: "system", content: text },
        ]);
        if (isQueryRunning) {
          // Inject into the current running query for immediate processing
          transport.sendEvent(InkToCore.USER_MESSAGE, { text });
        } else {
          setIsQueryRunning(true);
          setQueryStartMs(Date.now());
          lastPurposeRef.current = "";
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

  const handlePermissionModeCycle = useCallback(() => {
    transport.sendEvent(InkToCore.PERMISSION_MODE_CYCLE, {});
  }, [transport]);

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
      {/* Disconnection warning */}
      {isDisconnected && (
        <Box marginTop={1} marginLeft={1}>
          <Text color="red" bold>Connection lost — backend disconnected. Press Ctrl+C to exit.</Text>
        </Box>
      )}
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
              <Text italic color="cyan">*thinking </Text>
              <Text dimColor>{formatElapsed(elapsedSec)}</Text>
              {thinkingText && (
                <Text dimColor color="gray"> {thinkingText.replace(/\n/g, " ").slice(0, 60)}{thinkingText.length > 60 ? "..." : ""}</Text>
              )}
              {!thinkingText && <Text italic color="cyan">...</Text>}
            </>
          )}
          {(bgTaskCount.bash > 0 || bgTaskCount.bash_done > 0) && (
            <Text dimColor> | bg: shell {makeBar(bgTaskCount.bash_done, bgTaskCount.bash + bgTaskCount.bash_done)} {bgTaskCount.bash_done}/{bgTaskCount.bash + bgTaskCount.bash_done}</Text>
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
      {!isQueryRunning && (bgTaskCount.bash > 0 || bgTaskCount.bash_done > 0) && (
        <Box marginTop={0} marginLeft={1}>
          <Text dimColor>bg: shell {makeBar(bgTaskCount.bash_done, bgTaskCount.bash + bgTaskCount.bash_done)} {bgTaskCount.bash_done}/{bgTaskCount.bash + bgTaskCount.bash_done}</Text>
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
          contextInfo={showContextBar ? contextInfo : null}
          permissionMode={permissionMode}
          onPermissionModeCycle={handlePermissionModeCycle}
        />
      )}
    </Box>
  );
}