/**
 * IPC Protocol — shared message type definitions.
 *
 * Wire format: JSON lines terminated by \n over Unix domain socket.
 *     {"type": "event_name", "id": "correlation-id?", "payload": {...}}\n
 *
 * This file mirrors the Python-side protocol.py 1:1.
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

  // State sync
  STATE_UPDATE: "state.update",
  STATE_SNAPSHOT: "state.snapshot",

  // Permission
  PERMISSION_REQUEST: "permission.request",

  // Context
  CONTEXT_INFO: "context.info",

  // Welcome
  WELCOME: "welcome",

  // Compact
  COMPACT_STARTED: "compact.started",
  COMPACT_COMPLETE: "compact.complete",

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
  UI_TOGGLE_THINKING: "ui.toggle_thinking",
  UI_RESIZE: "ui.resize",
  THEME_CHANGE: "theme.change",
  SHUTDOWN_ACK: "shutdown.ack",
} as const;

export type InkToCoreType = (typeof InkToCore)[keyof typeof InkToCore];

// ── Message ────────────────────────────────────────────────────────────────────

export interface Message {
  type: string;
  payload: Record<string, unknown>;
  id?: string;
}

// ── Payload types ──────────────────────────────────────────────────────────────

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

// ── Permission decision ────────────────────────────────────────────────────────

export const PermissionDecisionValue = {
  ALLOW: "allow",
  DENY: "deny",
  ALWAYS_ALLOW: "always_allow",
} as const;

export type PermissionDecisionValueType =
  (typeof PermissionDecisionValue)[keyof typeof PermissionDecisionValue];