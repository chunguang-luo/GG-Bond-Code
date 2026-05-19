/**
 * Markdown renderer for Ink — uses marked + marked-terminal + cli-highlight.
 *
 * marked-terminal renders markdown to ANSI-colored terminal output
 * (tables via cli-table3, headings/bold/italic via chalk).
 * cli-highlight provides syntax highlighting for code blocks.
 *
 * Two components:
 * - <Markdown>          : for completed text (useMemo-cached)
 * - <StreamingMarkdown> : for streaming text (re-parses every render, tolerant of incomplete tokens)
 */

import React, { useMemo } from "react";
import { Text } from "ink";
import chalk from "chalk";
import { Marked } from "marked";
import { markedTerminal } from "marked-terminal";

// Ensure chalk outputs ANSI sequences — marked-terminal relies on chalk
// for all styling (headings, bold, italic, etc.) and it checks chalk.level
// at render time. In Ink's TTY context this is usually > 0, but we force it
// to guarantee colored output.
chalk.level = 3;

const marked = new Marked();

// Step 1: Use marked-terminal for all rendering (tables, headings, bold, etc.)
marked.use(
  markedTerminal({
    showSectionPrefix: false,
    // Override all style functions with our chalk instance (level=3),
    // because marked-terminal's internal chalk may have level=0
    heading: chalk.green.bold,
    firstHeading: chalk.magenta.bold.underline,
    strong: chalk.bold,
    em: chalk.italic,
    codespan: chalk.yellow,
    code: chalk.yellow,
    blockquote: chalk.gray.italic,
    del: chalk.dim.gray.strikethrough,
    link: chalk.blue,
    href: chalk.blue.underline,
  })
);

// ── Sanitize incomplete streaming Markdown ────────────────────────────────

/**
 * Sanitize potentially incomplete streaming Markdown before parsing.
 *
 * Handles:
 * - Unclosed fenced code blocks (odd number of ``` lines)
 * - Trailing incomplete inline code (odd backticks on last line)
 * - Unclosed ** (bold) and * (italic) markers
 */
function sanitizeStreamingMd(raw: string): string {
  let text = raw;

  // Close unclosed fenced code blocks
  const fenceCount = (text.match(/^```/gm) || []).length;
  if (fenceCount % 2 !== 0) {
    text += "\n```";
  }

  // Close trailing unclosed inline backtick
  const lastNewline = text.lastIndexOf("\n");
  const lastLine = lastNewline >= 0 ? text.slice(lastNewline + 1) : text;
  const backtickCount = (lastLine.match(/(?<!`)`(?!`)/g) || []).length;
  if (backtickCount % 2 !== 0) {
    text += "`";
  }

  // Close unclosed ** (bold) and * (italic) across the entire text.
  text = closeUnclosedDelimiter(text, "\\*\\*\\*");
  text = closeUnclosedDelimiter(text, "\\*\\*");
  text = closeUnclosedDelimiter(text, "\\*");

  return text;
}

/**
 * Count occurrences of a markdown delimiter in text (outside of code spans)
 * and append a closing one if the count is odd.
 */
function closeUnclosedDelimiter(text: string, delimiterRe: string): string {
  const withoutCode = text.replace(/`[^`]*`/g, "");
  const matches = withoutCode.match(new RegExp(delimiterRe, "g"));
  const count = matches ? matches.length : 0;
  if (count % 2 !== 0) {
    const delim = delimiterRe.replace(/\\/g, "");
    text += delim;
  }
  return text;
}

// ── Components ─────────────────────────────────────────────────────────────

interface MarkdownProps {
  children: string;
}

export function Markdown({ children }: MarkdownProps) {
  if (!children || !children.trim()) return null;

  const rendered = useMemo(() => {
    try {
      return marked.parse(children) as string;
    } catch {
      return children;
    }
  }, [children]);

  return <Text>{rendered}</Text>;
}

export function StreamingMarkdown({ children }: MarkdownProps) {
  if (!children || !children.trim()) return null;

  try {
    const sanitized = sanitizeStreamingMd(children);
    const rendered = marked.parse(sanitized) as string;
    return <Text>{rendered}</Text>;
  } catch {
    return <Text>{children}</Text>;
  }
}
