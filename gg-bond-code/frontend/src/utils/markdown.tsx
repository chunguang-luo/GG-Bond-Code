/**
 * Markdown renderer for Ink — uses marked.lexer() to parse into token AST,
 * then renders with Ink's native <Text> component (bold, italic, color props).
 *
 * Two components:
 * - <Markdown>          : for completed text (useMemo-cached)
 * - <StreamingMarkdown> : for streaming text (re-parses every render, tolerant of incomplete tokens)
 */

import React, { useMemo } from "react";
import { Box, Text } from "ink";
import { marked, Tokens } from "marked";

// ── Token rendering helpers ──────────────────────────────────────────────────

/** Render inline tokens (text, strong, em, codespan, link, etc.) into <Text> elements */
function renderInlineTokens(
  tokens: Tokens.Generic[] | undefined,
  keyPrefix: string
): React.ReactNode[] {
  if (!tokens || tokens.length === 0) return [];

  const elements: React.ReactNode[] = [];

  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    const key = `${keyPrefix}-${i}`;

    switch (t.type) {
      case "text":
        elements.push(<Text key={key}>{(t as Tokens.Text).text}</Text>);
        break;

      case "strong":
        elements.push(
          <Text key={key} bold>
            {renderInlineTokens((t as Tokens.Strong).tokens, key)}
          </Text>
        );
        break;

      case "em":
        elements.push(
          <Text key={key} italic>
            {renderInlineTokens((t as Tokens.Em).tokens, key)}
          </Text>
        );
        break;

      case "codespan":
        elements.push(
          <Text key={key} color="yellow">
            {(t as Tokens.Codespan).text}
          </Text>
        );
        break;

      case "link":
        elements.push(
          <Text key={key} color="cyan" underline>
            {(t as Tokens.Link).text}
          </Text>
        );
        break;

      case "br":
        elements.push(<Text key={key}>{"\n"}</Text>);
        break;

      case "escape":
        elements.push(<Text key={key}>{(t as Tokens.Escape).text}</Text>);
        break;

      default:
        // Fallback: raw text
        elements.push(
          <Text key={key}>{t.raw || ""}</Text>
        );
        break;
    }
  }

  return elements;
}

/** Render a single block-level token */
function renderBlockToken(token: Tokens.Generic, key: string): React.ReactNode {
  switch (token.type) {
    case "heading": {
      const h = token as Tokens.Heading;
      const color = h.depth <= 2 ? "green" : h.depth === 3 ? "cyan" : undefined;
      return (
        <Box key={key} marginTop={1}>
          <Text bold color={color}>
            {renderInlineTokens(h.tokens, key)}
          </Text>
        </Box>
      );
    }

    case "paragraph": {
      const p = token as Tokens.Paragraph;
      return (
        <Box key={key}>
          <Text>{renderInlineTokens(p.tokens, key)}</Text>
        </Box>
      );
    }

    case "code": {
      const c = token as Tokens.Code;
      return (
        <Box key={key} flexDirection="column" marginTop={0} marginBottom={0}>
          {c.lang && (
            <Text dimColor>
              {"  "}{c.lang}
            </Text>
          )}
          {c.text.split("\n").map((line, i) => (
            <Text key={`${key}-line-${i}`} color="yellow">
              {"  "}{line}
            </Text>
          ))}
        </Box>
      );
    }

    case "blockquote": {
      const bq = token as Tokens.Blockquote;
      return (
        <Box key={key} marginLeft={2} borderStyle="single" borderLeft borderColor="gray" paddingLeft={1}>
          <Text dimColor italic>
            {renderBlockTokens(bq.tokens, key)}
          </Text>
        </Box>
      );
    }

    case "list": {
      const list = token as Tokens.List;
      return (
        <Box key={key} flexDirection="column" marginLeft={2}>
          {list.items.map((item, i) => {
            const bullet = list.ordered ? `${list.start || 1 + i}. ` : "• ";
            return (
              <Box key={`${key}-item-${i}`}>
                <Text dimColor>{bullet}</Text>
                <Text>{renderInlineTokens(item.tokens, `${key}-item-${i}`)}</Text>
              </Box>
            );
          })}
        </Box>
      );
    }

    case "hr":
      return (
        <Box key={key}>
          <Text dimColor>{"─".repeat(40)}</Text>
        </Box>
      );

    case "table": {
      const tbl = token as Tokens.Table;
      const colCount = tbl.header.length;
      const colWidth = Math.floor(76 / Math.max(colCount, 1));

      return (
        <Box key={key} flexDirection="column">
          {/* Header */}
          <Box>
            {tbl.header.map((cell, ci) => (
              <Text key={`${key}-h-${ci}`} bold width={colWidth}>
                {cell.text}
              </Text>
            ))}
          </Box>
          {/* Separator */}
          <Text dimColor>{"─".repeat(colCount * colWidth)}</Text>
          {/* Rows */}
          {tbl.rows.map((row, ri) => (
            <Box key={`${key}-row-${ri}`}>
              {row.map((cell, ci) => (
                <Text key={`${key}-r-${ri}-${ci}`} width={colWidth}>
                  {cell.text}
                </Text>
              ))}
            </Box>
          ))}
        </Box>
      );
    }

    case "space":
      return null;

    default:
      // Unknown block token: render raw text
      return (
        <Box key={key}>
          <Text>{token.raw || ""}</Text>
        </Box>
      );
  }
}

/** Render an array of block-level tokens */
function renderBlockTokens(tokens: Tokens.Generic[], keyPrefix: string): React.ReactNode[] {
  const elements: React.ReactNode[] = [];
  for (let i = 0; i < tokens.length; i++) {
    const el = renderBlockToken(tokens[i], `${keyPrefix}-${i}`);
    if (el !== null) elements.push(el);
  }
  return elements;
}

// ── Sanitize incomplete streaming Markdown ─────────────────────────────────────

/**
 * Sanitize potentially incomplete streaming Markdown before lexing.
 *
 * Handles:
 * - Unclosed fenced code blocks (odd number of ``` lines)
 * - Trailing incomplete inline code (odd backticks on last line)
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

  return text;
}

// ── Components ────────────────────────────────────────────────────────────────

interface MarkdownProps {
  children: string;
}

export function Markdown({ children }: MarkdownProps) {
  if (!children || !children.trim()) return null;

  const elements = useMemo(() => {
    try {
      const tokens = marked.lexer(children);
      return renderBlockTokens(tokens as Tokens.Generic[], "md");
    } catch {
      return [<Text key="fallback">{children}</Text>];
    }
  }, [children]);

  return <Box flexDirection="column">{elements}</Box>;
}

export function StreamingMarkdown({ children }: MarkdownProps) {
  if (!children || !children.trim()) return null;

  try {
    const sanitized = sanitizeStreamingMd(children);
    const tokens = marked.lexer(sanitized);
    const elements = renderBlockTokens(tokens as Tokens.Generic[], "smd");
    return <Box flexDirection="column">{elements}</Box>;
  } catch {
    return (
      <Box flexDirection="column">
        <Text>{children}</Text>
      </Box>
    );
  }
}
