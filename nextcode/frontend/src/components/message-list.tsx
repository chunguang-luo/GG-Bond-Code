/**
 * MessageList — scrollable message list for the REPL.
 *
 * Displays all conversation messages with Markdown rendering.
 */

import React from "react";
import { Box, Text, useStdout, Static } from "ink";
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

/** Shorten an absolute file path to a relative-looking display path. */
function shortPath(absPath: string): string {
  // Try to strip common project prefixes
  const segments = absPath.replace(/\\/g, "/").split("/");
  // Find "src" or "next_code" and show from there
  const srcIdx = segments.indexOf("src");
  if (srcIdx >= 0 && srcIdx < segments.length - 1) {
    return segments.slice(srcIdx + 1).join("/");
  }
  // Fallback: show last 3 segments
  if (segments.length > 3) {
    return segments.slice(-3).join("/");
  }
  return segments[segments.length - 1] || absPath;
}

function formatToolLabel(name: string, input?: Record<string, unknown>): string {
  // Edit/Write: show as Update/Create with short path
  if (name === "Edit" && input?.file_path) {
    return `Update(${shortPath(String(input.file_path))})`;
  }
  if (name === "Write" && input?.file_path) {
    return `Create(${shortPath(String(input.file_path))})`;
  }

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

/** Check if a character is an East Asian wide character (takes 2 columns). */
function isWideChar(code: number): boolean {
  return code >= 0x1100 && (
    code <= 0x115f ||
    (code >= 0x2e80 && code <= 0xa4cf && code !== 0x303f) ||
    (code >= 0xac00 && code <= 0xd7a3) ||
    (code >= 0xf900 && code <= 0xfaff) ||
    (code >= 0xfe30 && code <= 0xfe6f) ||
    (code >= 0xff01 && code <= 0xff60) ||
    (code >= 0xffe0 && code <= 0xffe6) ||
    (code >= 0x20000 && code <= 0x2fffd) ||
    (code >= 0x30000 && code <= 0x3fffd)
  );
}

/** Measure the visible width of a string (accounting for East Asian wide chars). */
function visualWidth(text: string): number {
  let w = 0;
  for (const char of text) {
    w += isWideChar(char.charCodeAt(0)) ? 2 : 1;
  }
  return w;
}

/**
 * Split a string into visual lines that fit within `maxWidth` visible columns.
 * Accounts for East Asian wide characters (CJK etc.) which take 2 columns.
 * Returns an array of substrings, each with visible width <= maxWidth.
 */
function splitVisualLines(text: string, maxWidth: number): string[] {
  if (maxWidth <= 0) return [text];
  const lines: string[] = [];
  let current = "";
  let currentWidth = 0;

  for (const char of text) {
    const charWidth = isWideChar(char.charCodeAt(0)) ? 2 : 1;

    if (currentWidth + charWidth > maxWidth && current.length > 0) {
      lines.push(current);
      current = char;
      currentWidth = charWidth;
    } else {
      current += char;
      currentWidth += charWidth;
    }
  }
  if (current.length > 0 || lines.length === 0) {
    lines.push(current);
  }
  return lines;
}

/** Simple keyword highlighting for diff code lines.
 *  Highlights common keywords in cyan within the code content. */
function highlightCodeInDiff(code: string): React.ReactNode[] {
  // Common keywords to highlight
  const keywords = /\b(def|class|return|if|else|elif|for|while|import|from|as|with|try|except|finally|raise|yield|async|await|lambda|pass|break|continue|and|or|not|in|is|None|True|False|const|let|var|function|type|interface|export|default|extends|implements|new|this|super|static|public|private|protected|abstract|override|readonly|enum|namespace|require|module|throw|catch|finally|switch|case|func|struct|impl|trait|pub|fn|mut|use|mod|match|loop|go|chan|select|defer|range|map|make|append|println|fmt|err|nil|err)\b/g;

  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let keyCount = 0;

  while ((match = keywords.exec(code)) !== null) {
    // Text before keyword
    if (match.index > lastIndex) {
      parts.push(code.slice(lastIndex, match.index));
    }
    // Keyword highlighted in cyan
    parts.push(<Text key={`k${keyCount++}`} color="cyan">{match[0]}</Text>);
    lastIndex = match.index + match[0].length;
  }

  // Remaining text
  if (lastIndex < code.length) {
    parts.push(code.slice(lastIndex));
  }

  return parts.length > 0 ? parts : [code];
}

/** Gutter layout constants for diff lines.
 *  Gutter format: "#### + " or "#### - " or "####   "
 *  4-char line number + space + 1-char sign + space = 7 chars total */
const GUTTER_WIDTH = 7; // "1234 + "
const MARGIN_LEFT = 4;

/** Render a single diff visual line with background highlight and padding.
 *  Each visual line is a complete row: gutter(7) + content + padding.
 *  The renderer controls all layout — Ink's auto-wrap is disabled (wrap="truncate-end").
 *  First line gets the real gutter (lineNum + sign), continuation lines get
 *  an aligned blank gutter so the background color and padding look right. */
function renderDiffVisualLine(
  keyIdx: number,
  gutter: string,          // e.g. "  91 +" or "     +"
  content: string,         // the visible text for this visual line
  bgColor: string,
  contentMaxWidth: number, // max visible width for the content area
): React.ReactNode {
  const highlighted = highlightCodeInDiff(content);
  const contentVisualWidth = visualWidth(content);
  const paddingNeeded = Math.max(0, contentMaxWidth - contentVisualWidth);

  // wrap="truncate-end" disables Ink's auto word-wrap — we control line splitting
  return (
    <Text key={keyIdx} backgroundColor={bgColor} color="white" wrap="truncate-end">
      {gutter}{highlighted}{" ".repeat(paddingNeeded)}
    </Text>
  );
}

/** Parse unified diff lines into displayable colored lines with full-line background highlights and line numbers.
 *  The renderer splits long logical lines into visual lines so the terminal
 *  never does its own wrapping — we control all layout. */
function DiffLines({ diff, columns }: { diff: string; columns: number }) {
  if (!diff) return null;
  const allLines = diff.split("\n");
  const displayLines = allLines.slice(0, 20);

  // Full row = margin(4) + gutter(7) + content
  // Leave 1 column safety margin (terminal cursor / border edge)
  const contentMaxWidth = Math.max(columns - MARGIN_LEFT - GUTTER_WIDTH - 1, 20);

  let oldLine = 0;
  let newLine = 0;

  const rendered: React.ReactNode[] = [];
  let keyIdx = 0;
  let visualLineCount = 0;
  const maxVisualLines = 20; // limit total visual lines to avoid flooding

  for (const line of displayLines) {
    if (line === "") continue;

    if (line.startsWith("@@")) {
      const match = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      if (match) {
        oldLine = parseInt(match[1], 10);
        newLine = parseInt(match[2], 10);
      }
      continue;
    }

    if (line.startsWith("+") && !line.startsWith("+++")) {
      const ln = newLine;
      newLine++;
      const lnStr = ln > 0 ? String(ln).padStart(4) : "    ";
      // Diff lines are "+ content" — slice(1) keeps the leading space from diff format.
      // Strip that first space so gutter can provide a uniform " + " separator.
      const rawContent = line.slice(1);
      const codeContent = rawContent.startsWith(" ") ? rawContent.slice(1) : rawContent;
      const gutter = `${lnStr} + `;  // 7 chars: "#### + "
      const contGutter = "      + "; // 7 chars: spaces + sign + space

      const visualLines = splitVisualLines(codeContent, contentMaxWidth);
      for (let vi = 0; vi < visualLines.length; vi++) {
        if (visualLineCount >= maxVisualLines) break;
        rendered.push(
          renderDiffVisualLine(
            keyIdx++,
            vi === 0 ? gutter : contGutter,
            visualLines[vi],
            "#006000",
            contentMaxWidth,
          )
        );
        visualLineCount++;
      }
      continue;
    }

    if (line.startsWith("-") && !line.startsWith("---")) {
      const ln = oldLine;
      oldLine++;
      const lnStr = ln > 0 ? String(ln).padStart(4) : "    ";
      const rawContent = line.slice(1);
      const codeContent = rawContent.startsWith(" ") ? rawContent.slice(1) : rawContent;
      const gutter = `${lnStr} - `;  // 7 chars
      const contGutter = "      - "; // 7 chars

      const visualLines = splitVisualLines(codeContent, contentMaxWidth);
      for (let vi = 0; vi < visualLines.length; vi++) {
        if (visualLineCount >= maxVisualLines) break;
        rendered.push(
          renderDiffVisualLine(
            keyIdx++,
            vi === 0 ? gutter : contGutter,
            visualLines[vi],
            "#5f0000",
            contentMaxWidth,
          )
        );
        visualLineCount++;
      }
      continue;
    }

    // Context line — also wrap if needed
    // Context lines in diff: " content" — the leading space is not indentation, it's diff format.
    // Strip it, then gutter provides uniform spacing.
    const ctxOldLine = oldLine;
    const ctxNewLine = newLine;
    oldLine++;
    newLine++;
    const rawCtx = line.startsWith(" ") ? line.slice(1) : line;
    const ctxContent = rawCtx.startsWith(" ") ? rawCtx.slice(1) : rawCtx;
    const lnCtx = ctxOldLine > 0 ? String(ctxOldLine).padStart(4) + "   " : "       "; // 7 chars: "####   "
    const contGutterCtx = "       "; // 7 spaces
    const visualLines = splitVisualLines(ctxContent, contentMaxWidth);
    for (let vi = 0; vi < visualLines.length; vi++) {
      if (visualLineCount >= maxVisualLines) break;
      const g = vi === 0 ? lnCtx : contGutterCtx;
      rendered.push(<Text key={keyIdx++} dimColor wrap="truncate-end">{g}{visualLines[vi]}</Text>);
      visualLineCount++;
    }

    if (visualLineCount >= maxVisualLines) break;
  }

  if (rendered.length === 0) return null;
  return <Box marginLeft={MARGIN_LEFT} flexDirection="column">{rendered}</Box>;
}

/** Agent start message — static display (no live timer).
 *  Since messages are rendered inside <Static>, they must not change after
 *  initial render. The agent_start message shows only the type and prompt.
 *  Final status (Done + elapsed + tool count) is shown by the agent_result message. */
function AgentStartItem({ msg }: { msg: DisplayMessage }) {
  const agentMeta = msg.metadata as {
    agent_type?: string; description?: string; prompt?: string;
  } | undefined;
  const rawType = agentMeta?.agent_type || "Agent";
  const agentType = rawType === "general-purpose" ? rawType : `${rawType} Agent`;
  const agentPrompt = agentMeta?.prompt || "";
  return (
    <Box marginTop={1} marginLeft={2} flexDirection="column">
      <Box>
        <Text color="magenta" bold>{`⏎ ${agentType}`}</Text>
      </Box>
      {agentPrompt && (
        <Box marginLeft={4}>
          <Text dimColor>{agentPrompt}</Text>
        </Box>
      )}
    </Box>
  );
}

function MessageItem({ msg, columns }: { msg: DisplayMessage; columns: number }) {
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
        <Box marginTop={1} marginBottom={0} flexDirection="column">
          <Box>
            <Text backgroundColor="gray" color="white" bold>{"> "}</Text>
            <Text backgroundColor="gray" color="white">{" " + msg.content + " "}</Text>
          </Box>
          <Text>{" "}</Text>
        </Box>
      );

    case "text": {
      if (!msg.content.trim()) return null;
      return (
        <Box flexDirection="column">
          <Box flexDirection="row">
            <Text>{"⏺ "}</Text>
            <Markdown trim>{msg.content}</Markdown>
          </Box>
          <Text>{" "}</Text>
        </Box>
      );
    }

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
        <Box>
          <Text color="cyan">⚙ </Text>
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
      // Edit/Write: show diff stats + diff content
      const diffMeta = msg.metadata as { added?: number; removed?: number; diff?: string } | undefined;
      if ((msg.toolName === "Edit" || msg.toolName === "Write") && diffMeta) {
        const added = diffMeta.added ?? 0;
        const removed = diffMeta.removed ?? 0;
        const statsParts: string[] = [];
        if (added > 0) statsParts.push(`Added ${added} line${added !== 1 ? "s" : ""}`);
        if (removed > 0) statsParts.push(`removed ${removed} line${removed !== 1 ? "s" : ""}`);
        const stats = statsParts.join(", ");
        return (
          <Box marginLeft={4} flexDirection="column">
            <Text dimColor>{`⎿  ${stats}${elapsedStr}`}</Text>
            {diffMeta.diff && <DiffLines diff={diffMeta.diff} columns={columns} />}
            <Text>{" "}</Text>
          </Box>
        );
      }
      const summary = msg.toolResult ? formatToolResult(msg.toolResult) : "Done";
      return (
        <Box marginLeft={4} flexDirection="column">
          <Text dimColor>⎿  {summary}{elapsedStr}</Text>
          <Text>{" "}</Text>
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
      const elapsed = resultMeta?._elapsed || "";
      const toolCount = resultMeta?.tool_use_count || 0;
      return (
        <Box marginLeft={2}>
          <Text color="magenta" bold>{`⏎ ${agentType}`}</Text>
          <Text dimColor> Done.</Text>
          {elapsed && <Text dimColor>{` (${elapsed})`}</Text>}
          {toolCount > 0 && <Text dimColor>{` · ${toolCount} tools used`}</Text>}
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

export const MessageList = React.memo(function MessageList({ messages, currentText }: MessageListProps) {
  const { stdout } = useStdout();
  const columns = stdout?.columns || 120;
  return (
    <Box flexDirection="column" flexGrow={1} overflowY="hidden">
      {/* Completed messages rendered via <Static> — written once, never erased.
          This prevents Ink's erase-and-redraw cycle from scrolling the terminal
          back to the bottom when the user is reading earlier messages. */}
      <Static items={messages}>
        {(msg, idx) => (
          <MessageItem key={`${msg.type}-${idx}`} msg={msg} columns={columns} />
        )}
      </Static>
      {/* Streaming text stays in the dynamic area — erased and redrawn each tick */}
      {currentText && (
        <Box marginTop={1} flexDirection="row">
          <Text dimColor>{"⏺ "}</Text>
          <StreamingMarkdown>{currentText}</StreamingMarkdown>
        </Box>
      )}
    </Box>
  );
});
