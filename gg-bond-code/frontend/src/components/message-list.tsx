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
  type: "text" | "thinking" | "tool_use" | "tool_result" | "error" | "warning" | "system";
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
  const priorityKeys = ["file_path", "path", "command", "pattern", "query", "url", "name"];
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

function MessageItem({ msg }: { msg: DisplayMessage }) {
  switch (msg.type) {
    case "system":
      // User's question — Claude Code style
      return (
        <Box marginTop={1}>
          <Text color="green" bold>{"> "} </Text>
          <Text color="green">{msg.content}</Text>
        </Box>
      );

    case "text":
      return <Markdown>{msg.content}</Markdown>;

    case "thinking":
      return (
        <Box flexDirection="column" marginBottom={0}>
          <Text dimColor>Thinking: {msg.content.slice(0, 200)}</Text>
        </Box>
      );

    case "tool_use": {
      const label = formatToolLabel(msg.toolName || "Tool", msg.toolInput);
      return (
        <Box marginTop={1}>
          <Text color="cyan">{"  "}⚙ </Text>
          <Text bold>{label}</Text>
        </Box>
      );
    }

    case "tool_result": {
      const elapsed = msg.elapsedMs ? ` (${msg.elapsedMs}ms)` : "";
      if (msg.toolError) {
        return (
          <Box marginLeft={4} flexDirection="column">
            <Text color="red">⎿  Error{elapsed}</Text>
            {msg.toolResult && (
              <Text color="red" dimColor>
                {msg.toolResult.split("\n").slice(0, 5).join("\n")}
              </Text>
            )}
          </Box>
        );
      }
      const summary = msg.toolResult ? formatToolResult(msg.toolResult) : "Done";
      return (
        <Box marginLeft={4}>
          <Text dimColor>⎿  {summary}{elapsed}</Text>
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

    default:
      return null;
  }
}

export function MessageList({ messages, currentText }: MessageListProps) {
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