/**
 * MessageList — scrollable message list for the REPL.
 *
 * Displays all conversation messages with Markdown rendering.
 */

import React from "react";
import { Box, Text } from "ink";
import { Markdown, StreamingMarkdown } from "../utils/markdown";

export interface DisplayMessage {
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

interface MessageListProps {
  messages: DisplayMessage[];
  currentText: string;
}

function formatToolLabel(name: string, input?: Record<string, unknown>): string {
  const priorityKeys = ["file_path", "path", "command", "pattern", "query", "url", "name", "prompt"];
  for (const key of priorityKeys) {
    if (input && input[key] !== undefined) {
      const val = String(input[key]);
      return `${name}(${val.length > 80 ? val.slice(0, 77) + "..." : val})`;
    }
  }
  return `${name}()`;
}

function formatToolResult(result: string): string {
  if (!result.trim()) return "Done";
  const lines = result.split("\n");
  if (lines.length > 3) return `${lines.length} lines`;
  if (result.length > 100) return result.slice(0, 97) + "...";
  return result;
}

function formatElapsed(ms: number | undefined): string {
  if (!ms) return "";
  if (ms < 1000) return `${ms}ms`;
  const sec = Math.round(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}m ${s}s`;
}

/** Format milliseconds as "Xm Ys" or "Ys". */
function formatMs(ms: number): string {
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}m ${s}s`;
}

/** Hook that returns elapsed seconds since startMs, updating every second. */
function useAgentElapsed(startMs: number | null): number {
  const [elapsed, setElapsed] = React.useState(0);
  React.useEffect(() => {
    if (startMs === null) {
      setElapsed(0);
      return;
    }
    const update = () => setElapsed(Date.now() - startMs!);
    update();
    const id = setInterval(update, 1000);
    return () => clearInterval(id);
  }, [startMs]);
  return elapsed;
}

/** Agent start message with live elapsed timer, updates to Done on completion. */
function AgentStartItem({ msg }: { msg: DisplayMessage }) {
  const agentMeta = msg.metadata as {
    agent_type?: string; description?: string; prompt?: string;
    _startMs?: number; _tool_use_count?: number;
    _done?: boolean; _finalElapsed?: string;
  } | undefined;
  const rawType = agentMeta?.agent_type || "Agent";
  const agentType = rawType === "general-purpose" ? rawType : `${rawType} Agent`;
  const agentPrompt = agentMeta?.prompt || "";
  const startMs = agentMeta?._startMs || null;
  const toolCount = agentMeta?._tool_use_count || 0;
  const isDone = agentMeta?._done || false;
  const finalElapsed = agentMeta?._finalElapsed || "";
  const elapsedMs = useAgentElapsed(isDone ? null : startMs);
  return (
    <Box marginTop={1} marginLeft={2} flexDirection="column">
      <Box>
        <Text color="magenta" bold>{`⏎ ${agentType}`}</Text>
        {isDone && finalElapsed && <Text dimColor>{` (${finalElapsed})`}</Text>}
        {!isDone && elapsedMs > 0 && <Text dimColor>{` (${formatMs(elapsedMs)})`}</Text>}
      </Box>
      <Box marginLeft={2}>
        {isDone ? (
          <>
            <Text dimColor>⎿  Done</Text>
            <Text dimColor>{` · ${toolCount} tools used`}</Text>
          </>
        ) : (
          <>
            <Text dimColor>⎿  Running</Text>
            <Text dimColor>{` · ${toolCount} tools used`}</Text>
          </>
        )}
      </Box>
      {agentPrompt && (
        <Box marginLeft={4}>
          <Text dimColor>{agentPrompt}</Text>
        </Box>
      )}
    </Box>
  );
}

function MessageItem({ msg }: { msg: DisplayMessage }) {
  switch (msg.type) {
    case "queued":
      // Queued user message — waiting for current task to finish
      return (
        <Box marginTop={1} marginBottom={0}>
          <Text backgroundColor="gray" color="white" bold>{"> "}</Text>
          <Text backgroundColor="gray" color="white">{" " + msg.content + " "}</Text>
          <Text dimColor color="yellow">{" ⏳"}</Text>
        </Box>
      );

    case "system":
      // User's question — gray background, separated from answer
      return (
        <Box marginTop={1} marginBottom={0}>
          <Text backgroundColor="gray" color="white" bold>{"> "}</Text>
          <Text backgroundColor="gray" color="white">{" " + msg.content + " "}</Text>
        </Box>
      );

    case "text":
      return (
        <Box marginTop={1}>
          <Markdown>{msg.content}</Markdown>
        </Box>
      );

    case "info":
      return (
        <Box marginTop={0} marginLeft={2}>
          <Text dimColor>{msg.content}</Text>
        </Box>
      );

    case "command":
      return (
        <Box marginTop={1} marginBottom={0}>
          <Text backgroundColor="gray" color="white">{msg.content}</Text>
        </Box>
      );

    case "thinking":
      return (
        <Box flexDirection="column" marginBottom={0}>
          <Text dimColor>Thinking: {msg.content.slice(0, 200)}</Text>
        </Box>
      );

    case "tool_use": {
      // Agent tool: skip rendering — agent_start will show type + prompt
      if (msg.toolName === "Agent") {
        return null;
      }
      const label = formatToolLabel(msg.toolName || "Tool", msg.toolInput);
      return (
        <Box marginTop={1}>
          <Text color="cyan">{"  "}⚙ </Text>
          <Text bold>{label}</Text>
        </Box>
      );
    }

    case "tool_result": {
      const elapsed = formatElapsed(msg.elapsedMs);
      const elapsedStr = elapsed ? ` (${elapsed})` : "";
      if (msg.toolError) {
        return (
          <Box marginLeft={4} flexDirection="column">
            <Text color="red">⎿  Error{elapsedStr}</Text>
            {msg.toolResult && (
              <Text color="red" dimColor>
                {msg.toolResult.split("\n").slice(0, 5).join("\n")}
              </Text>
            )}
          </Box>
        );
      }
      // Agent tool results: 只显示 Done + 耗时
      if (msg.toolName === "Agent") {
        return null;
      }
      const summary = msg.toolResult ? formatToolResult(msg.toolResult) : "Done";
      return (
        <Box marginLeft={4}>
          <Text dimColor>⎿  {summary}{elapsedStr}</Text>
        </Box>
      );
    }

    case "error":
      return (
        <Box marginTop={1}>
          <Text color="red" bold>
            Error: {msg.content.slice(0, 200)}
          </Text>
        </Box>
      );

    case "warning": {
      const meta = msg.metadata as { percentUsed?: number; level?: string } | undefined;
      const pct = meta?.percentUsed ?? 0;
      const barLen = 20;
      const filled = Math.min(barLen, Math.round((barLen * pct) / 100));
      const bar = "█".repeat(filled) + "░".repeat(barLen - filled);
      const color = meta?.level === "blocking" ? "red" : meta?.level === "error" ? "red" : "yellow";
      return (
        <Box marginTop={1}>
          <Text color={color}>
            Context: [{bar}] {pct}% — {msg.content.slice(0, 100)}
          </Text>
        </Box>
      );
    }

    case "agent_start": {
      return <AgentStartItem msg={msg} />;
    }

    case "agent_tool_use": {
      const label = formatToolLabel(msg.toolName || "Tool", msg.toolInput);
      return (
        <Box marginLeft={4}>
          <Text color="magenta" dimColor>{"  "}⚙ </Text>
          <Text dimColor>{label}</Text>
        </Box>
      );
    }

    case "agent_tool_result": {
      const elapsed = formatElapsed(msg.elapsedMs);
      const elapsedStr = elapsed ? ` (${elapsed})` : "";
      if (msg.toolError) {
        return (
          <Box marginLeft={6}>
            <Text color="red" dimColor>⎿  {msg.toolName || "Error"}: {elapsedStr || "failed"}</Text>
          </Box>
        );
      }
      const summary = msg.toolResult ? formatToolResult(msg.toolResult) : "Done";
      return (
        <Box marginLeft={6}>
          <Text dimColor>⎿  {msg.toolName}: {summary}{elapsedStr}</Text>
        </Box>
      );
    }

    case "agent_result": {
      const resultMeta = msg.metadata as { _elapsed?: string; agent_type?: string; tool_use_count?: number } | undefined;
      const rawType = resultMeta?.agent_type || "Agent";
      const agentType = rawType === "general-purpose" ? rawType : `${rawType} Agent`;
      return (
        <Box marginLeft={2}>
          <Text color="magenta" bold>{`⏎ ${agentType}`}</Text>
          <Text dimColor> Done.</Text>
        </Box>
      );
    }

    case "task_notification": {
      const meta = msg.metadata as { result?: string; isFailed?: boolean; isStarted?: boolean; description?: string } | undefined;
      const desc = meta?.description || "";
      // Started notification — yellow with ⎿ description
      if (meta?.isStarted) {
        return (
          <Box marginTop={1} marginLeft={2} flexDirection="column">
            <Text color="yellow" dimColor>{msg.content}</Text>
            {desc && (
              <Box marginLeft={2}>
                <Text dimColor>{`⎿  ${desc}`}</Text>
              </Box>
            )}
          </Box>
        );
      }
      // Completion notification — green/red with ⎿ description + result preview
      const resultText = meta?.result || "";
      const iconColor = meta?.isFailed ? "red" : "green";
      // Take first 10 non-empty lines of result
      const resultLines = resultText.split("\n").filter((l: string) => l.trim());
      const previewLines = resultLines.slice(0, 10);
      const truncated = resultLines.length > 10;
      return (
        <Box marginTop={1} marginLeft={2} flexDirection="column">
          <Text bold color={iconColor}>{msg.content}</Text>
          {desc && (
            <Box marginLeft={2}>
              <Text dimColor>{`⎿  ${desc}`}</Text>
            </Box>
          )}
          {previewLines.length > 0 && (
            <Box marginLeft={2} flexDirection="column">
              {previewLines.map((line: string, i: number) => (
                <Text key={i} dimColor>{line}</Text>
              ))}
              {truncated && <Text dimColor>...</Text>}
            </Box>
          )}
        </Box>
      );
    }

    default:
      return null;
  }
}

export function MessageList({ messages, currentText, agentText }: MessageListProps) {
  return (
    <Box flexDirection="column" flexGrow={1} overflowY="hidden">
      {messages.map((msg, idx) => (
        // Use index-based key to avoid duplicate IDs between tool_use and tool_result
        <MessageItem key={`${msg.type}-${idx}`} msg={msg} />
      ))}
      {/* Streaming text: real-time Markdown rendering with incomplete-token tolerance */}
      {currentText && <StreamingMarkdown>{currentText}</StreamingMarkdown>}
    </Box>
  );
}