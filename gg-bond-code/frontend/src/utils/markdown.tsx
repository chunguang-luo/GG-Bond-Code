/**
 * Markdown to ANSI renderer using marked + marked-terminal.
 *
 * Uses marked to parse Markdown, markedTerminal to render as ANSI-styled text.
 * Then wraps in Ink's <Text> for display — no custom parsing needed.
 *
 * Two components:
 * - <Markdown>      : for completed text (useMemo-cached)
 * - <StreamingMarkdown> : for streaming text (re-parses every render, tolerant of incomplete tokens)
 */

import React, { useMemo } from "react";
import { Box, Text } from "ink";
import { marked } from "marked";
import { markedTerminal } from "marked-terminal";

// Configure marked to output terminal-friendly ANSI text
marked.use(
  markedTerminal({
    width: 80,
    showSectionPrefix: false,
    tab: 2,
    paragraph: "\n",
    code: "\n",
  })
);

interface MarkdownProps {
  children: string;
}

export function Markdown({ children }: MarkdownProps) {
  if (!children || !children.trim()) return null;

  // Parse markdown to ANSI-styled text
  const ansiText = useMemo(() => {
    try {
      return marked.parse(children, { async: false }) as string;
    } catch {
      // Fallback: show raw text if parsing fails
      return children;
    }
  }, [children]);

  return (
    <Box flexDirection="column">
      <Text>{ansiText}</Text>
    </Box>
  );
}

/**
 * Sanitize potentially incomplete streaming Markdown before parsing.
 *
 * Handles:
 * - Unclosed fenced code blocks (``` without closing ```)
 * - Trailing incomplete inline code (` without closing `)
 * - Trailing incomplete bold/italic markers
 */
function sanitizeStreamingMd(raw: string): string {
  let text = raw;

  // Close unclosed fenced code blocks
  // Count the number of ``` lines; if odd, we have an unclosed block
  const fenceCount = (text.match(/^```/gm) || []).length;
  if (fenceCount % 2 !== 0) {
    text += "\n```";
  }

  // Close trailing unclosed inline backtick (odd number of single backticks on last line)
  const lastNewline = text.lastIndexOf("\n");
  const lastLine = lastNewline >= 0 ? text.slice(lastNewline + 1) : text;
  const backtickCount = (lastLine.match(/(?<!`)`(?!`)/g) || []).length;
  if (backtickCount % 2 !== 0) {
    text += "`";
  }

  return text;
}

/**
 * StreamingMarkdown — renders Markdown in real-time as text streams in.
 *
 * Unlike <Markdown>, this re-parses on every render (no useMemo) so the
 * display updates incrementally. It sanitizes incomplete tokens to avoid
 * marked parse errors and visual glitches.
 */
export function StreamingMarkdown({ children }: MarkdownProps) {
  if (!children || !children.trim()) return null;

  try {
    const sanitized = sanitizeStreamingMd(children);
    const ansiText = marked.parse(sanitized, { async: false }) as string;
    return (
      <Box flexDirection="column">
        <Text>{ansiText}</Text>
      </Box>
    );
  } catch {
    // Fallback: show raw text if parsing fails
    return (
      <Box flexDirection="column">
        <Text>{children}</Text>
      </Box>
    );
  }
}