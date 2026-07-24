/**
 * IPC Protocol — JSON-RPC 2.0 over pipe fd.
 *
 * Wire format: one JSON-RPC 2.0 message per line (notification or request).
 *     → Notification: {"jsonrpc":"2.0","method":"event.name","params":{...}}\n
 *     → Request:      {"jsonrpc":"2.0","id":1,"method":"event.name","params":{...}}\n
 *
 * Mirrors the Python-side protocol.py 1:1.
 */

// ── Python → Ink (Core → UI) ─────────────────────────────────────────────────

export const CoreToInk = {
  // Session lifecycle
  SESSION_READY: "session.ready",
  SESSION_SHUTDOWN: "session.shutdown",

  // Query events
  QUERY_TEXT_DELTA: "query.text_delta",
  QUERY_THINKING_DELTA: "query.thinking_delta",
  QUERY_TOOL_START: "query.tool_start",
  QUERY_TOOL_USE: "query.tool_use",
  QUERY_TOOL_RESULT: "query.tool_result",
  QUERY_ERROR: "query.error",
  QUERY_WARNING: "query.warning",
  QUERY_COMPLETE: "query.complete",
  QUERY_INFO: "query.info",
  QUERY_CLEARED: "query.cleared",
  QUERY_QUEUED: "query.queued",
  QUERY_DEQUEUE: "query.dequeue",

  // State sync
  STATE_UPDATE: "state.update",
  STATE_SNAPSHOT: "state.snapshot",

  // Permission
  PERMISSION_REQUEST: "permission.request",
  PERMISSION_MODE_UPDATE: "permission.mode_update",

  // Context
  CONTEXT_INFO: "context.info",

  // Welcome
  WELCOME: "welcome",

  // Compact
  COMPACT_STARTED: "compact.started",
  COMPACT_COMPLETE: "compact.complete",

  // Command list sync
  COMMANDS_UPDATE: "commands.update",

  // Agent lifecycle
  AGENT_START: "agent.start",
  AGENT_RESULT: "agent.result",

  // Agent real-time streaming
  AGENT_TEXT_DELTA: "agent.text_delta",
  AGENT_TOOL_USE: "agent.tool_use",
  AGENT_TOOL_RESULT: "agent.tool_result",
  AGENT_PROGRESS: "agent.progress",

  // Task events
  TASK_STARTED: "task.started",
  TASK_COMPLETED: "task.completed",
  TASK_FAILED: "task.failed",
  TASK_COUNT: "task.count",
  TASK_OUTPUT: "task.output",
  TASK_STALLED: "task.stalled",

  // Heartbeat
  PING: "ping",
} as const;

export type CoreToInkType = (typeof CoreToInk)[keyof typeof CoreToInk];

// ── Ink → Python (UI → Core) ─────────────────────────────────────────────────

export const InkToCore = {
  READY: "ready",
  PONG: "pong",
  USER_MESSAGE: "user.message",
  USER_INTERRUPT: "user.interrupt",
  USER_COMMAND: "user.command",
  PERMISSION_RESPONSE: "permission.response",
  PERMISSION_MODE_CYCLE: "permission.mode_cycle",
  UI_TOGGLE_THINKING: "ui.toggle_thinking",
  UI_RESIZE: "ui.resize",
  THEME_CHANGE: "theme.change",
  SHUTDOWN_ACK: "shutdown.ack",
  TASK_STOP: "task.stop",
  TASK_RETAIN: "task.retain",
} as const;

export type InkToCoreType = (typeof InkToCore)[keyof typeof InkToCore];

// ── JSON-RPC 2.0 Message ────────────────────────────────────────────────────

export interface JsonRpcNotification {
  jsonrpc: "2.0";
  method: string;
  params: Record<string, unknown>;
}

export interface JsonRpcRequest {
  jsonrpc: "2.0";
  id: number;
  method: string;
  params: Record<string, unknown>;
}

export interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: number;
  result?: Record<string, unknown>;
  error?: { code: number; message: string; data?: unknown };
}

// ── Legacy Message (backward compat) ─────────────────────────────────────────

export interface Message {
  type: string;
  payload: Record<string, unknown>;
  id?: string;
}

// ── Payload types ────────────────────────────────────────────────────────────

export interface SessionReadyPayload {
  model: string;
  cwd: string;
  projectRoot: string;
}

export interface ToolUsePayload {
  toolUseId: string;
  toolName: string;
  toolInput: Record<string, unknown>;
  toolPurpose: string;
}

export interface ToolResultPayload {
  toolUseId: string;
  toolName: string;
  toolResult: string;
  toolError: boolean;
  elapsedMs: number;
}

export interface WarningPayload {
  content: string;
  metadata: {
    level: "ok" | "warning" | "error" | "blocking";
    percentUsed: number;
    tokenUsage: number;
    effectiveWindow: number;
  };
}

export interface PermissionRequestPayload {
  requestId: string;
  toolName: string;
  params: Record<string, unknown>;
}

export interface PermissionModeUpdatePayload {
  mode: string;
}

export interface PermissionResponsePayload {
  requestId: string;
  decision: "allow" | "deny" | "always_allow";
  wildcard: boolean;
}

export interface ContextInfoPayload {
  model: string;
  contextWindow: number;
  tokenUsage: number;
  effectiveWindow: number;
  warningState: string;
  percentLeft: number;
}

export interface CommandInfo {
  name: string;
  description: string;
  source: string;
  aliases: string[];
}

export interface CommandsUpdatePayload {
  commands: CommandInfo[];
}

export interface WelcomePayload {
  model: string;
  cwd: string;
}

export interface ReadyPayload {
  version: string;
  terminalInfo: {
    columns: number;
    rows: number;
    supportsTrueColor: boolean;
    supportsSyncOutput: boolean;
    termProgram: string;
  };
}

// ── Task payloads ────────────────────────────────────────────────────────────

export interface TaskCountPayload {
  bash: number;
  agent: number;
}

export interface TaskStartedPayload {
  task_id: string;
  task_type: string;
  description: string;
}

export interface TaskCompletedPayload {
  task_id: string;
  task_type: string;
  status: string;
  result: string;
  description: string;
}

export interface TaskOutputPayload {
  task_id: string;
  output: string;
  is_running: boolean;
}

export interface TaskStalledPayload {
  task_id: string;
  prompt_type: string;
  message: string;
}

// ── Permission decision ──────────────────────────────────────────────────────

export const PermissionDecisionValue = {
  ALLOW: "allow",
  DENY: "deny",
  ALWAYS_ALLOW: "always_allow",
} as const;

export type PermissionDecisionValueType =
  (typeof PermissionDecisionValue)[keyof typeof PermissionDecisionValue];
