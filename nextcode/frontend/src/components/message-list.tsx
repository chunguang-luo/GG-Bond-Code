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
  type: "text" | "thinking" | "tool_use" | "tool_result" | "error" | "warning" | "system" | "info" | "command" | "agent_start" | "agent_tool_use" | "agent_tool_result" | "agent_result";
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
  agentText: string;
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

function MessageItem({ msg }: { msg: DisplayMessage }) {
  switch (msg.type) {
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
      const agentMeta = msg.metadata as { agent_type?: string; description?: string; prompt?: string } | undefined;
      const agentType = agentMeta?.agent_type || "Agent";
      const agentPrompt = agentMeta?.prompt || "";
      return (
        <Box marginTop={1} marginLeft={2} flexDirection="column">
          <Box>
            <Text color="magenta">⏎ </Text>
            <Text color="magenta" bold>{agentType}</Text>
          </Box>
          {agentPrompt && (
            <Box marginLeft={2}>
              <Text dimColor>{agentPrompt}</Text>
            </Box>
          )}
        </Box>
      );
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
      const resultMeta = msg.metadata as { _elapsed?: string; agent_type?: string } | undefined;
      const agentType = resultMeta?.agent_type || "Agent";
      const elapsed = resultMeta?._elapsed || "";
      return (
        <Box marginLeft={2} flexDirection="column">
          <Box>
            <Text dimColor>⎿  </Text>
            <Text color="magenta" bold>{agentType}</Text>
            <Text dimColor> finished</Text>
          </Box>
          {elapsed && (
            <Box marginLeft={4}>
              <Text dimColor>Done ({elapsed})</Text>
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
      {/* Sub-agent streaming text: Markdown rendering with indent */}
      {agentText && (
        <Box marginLeft={2}>
          <StreamingMarkdown>{agentText}</StreamingMarkdown>
        </Box>
      )}
    </Box>
  );
}