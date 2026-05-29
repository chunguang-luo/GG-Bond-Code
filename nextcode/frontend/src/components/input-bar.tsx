/**
 * InputBar — user input component with prompt.
 *
 * Handles text input, Enter to submit, Tab to complete slash commands,
 * arrow keys to move cursor, and displays completion suggestions.
 */

import React, { useMemo, useRef } from "react";
import { Box, Text, useInput } from "ink";
import { CommandInfo } from "../ipc/protocol";

interface InputState {
  value: string;
  cursor: number;
}

interface ContextBarInfo {
  tokenUsage: number;
  effectiveWindow: number;
  warningState: string;
}

/** Compute line index and column offset from a flat cursor position */
function getCursorLineCol(value: string, cursor: number): { line: number; col: number } {
  const lines = value.split("\n");
  let offset = 0;
  for (let i = 0; i < lines.length; i++) {
    const lineLen = lines[i].length;
    if (offset + lineLen >= cursor) {
      return { line: i, col: cursor - offset };
    }
    offset += lineLen + 1; // +1 for the \n
  }
  // Cursor at the very end
  const lastIdx = lines.length - 1;
  return { line: lastIdx, col: lines[lastIdx].length };
}

/** Compute flat cursor position from line index and column offset */
function cursorFromLineCol(value: string, line: number, col: number): number {
  const lines = value.split("\n");
  let offset = 0;
  for (let i = 0; i < line; i++) {
    offset += lines[i].length + 1;
  }
  return offset + Math.min(col, lines[line].length);
}

interface InputBarProps {
  inputState: InputState;
  setInputState: (value: InputState | ((prev: InputState) => InputState)) => void;
  onSubmit: (text: string) => void;
  disabled?: boolean;
  isQueryRunning?: boolean;
  model?: string;
  commands?: CommandInfo[];
  contextInfo?: ContextBarInfo | null;
  permissionMode?: string;
  onPermissionModeCycle?: () => void;
}

export function InputBar({
  inputState,
  setInputState,
  onSubmit,
  disabled,
  isQueryRunning,
  model,
  commands = [],
  contextInfo,
  permissionMode,
  onPermissionModeCycle,
}: InputBarProps) {
  // Build a flat list of all command names + aliases for Tab completion
  const allCommandNames = useMemo(() => {
    const names: string[] = [];
    for (const cmd of commands) {
      names.push(cmd.name);
      for (const alias of cmd.aliases) {
        names.push(alias);
      }
    }
    return names.sort();
  }, [commands]);

  // Build name→description lookup for the suggestion list
  const commandDescMap = useMemo(() => {
    const map = new Map<string, { description: string; source: string }>();
    for (const cmd of commands) {
      map.set(cmd.name, { description: cmd.description, source: cmd.source });
      for (const alias of cmd.aliases) {
        map.set(alias, { description: cmd.description, source: cmd.source });
      }
    }
    return map;
  }, [commands]);

  // Input history — persists across the session
  const historyRef = useRef<string[]>([]);
  const historyIndexRef = useRef(-1);  // -1 = not browsing history
  const draftRef = useRef("");  // Save current draft when entering history

  // Compute matching commands for the current input
  const matchingCommands = useMemo(() => {
    if (!inputState.value.startsWith("/")) return [];
    const matches = allCommandNames.filter((c) => c.startsWith(inputState.value));
    return [...new Set(matches)].slice(0, 8);
  }, [inputState.value, allCommandNames]);

  // Handle Shift+Enter / Option+Enter via useInput.
  // When Ink receives \x1b\r (Option+Enter), parseKeypress produces:
  //   key.name = '' (not 'return'), key.meta = true (from \x1b prefix)
  //   input = '\r' (the sequence after stripping \x1b)
  // So we check for key.meta + input === '\r' to detect Option+Enter.
  // For kitty/xterm sequences like \x1b[13;2u, parseKeypress produces:
  //   key.name from fnKeyRe parsing, with key.shift = true
  //   These won't match key.return, so they fall through to the else branch.

  useInput(
    (input, key) => {
      // Shift+Tab: cycle permission mode
      if (key.tab && key.shift && onPermissionModeCycle) {
        onPermissionModeCycle();
        return;
      }
      // Option+Enter (macOS): \x1b\r → key.meta=true, input='\r'
      // Shift+Enter (kitty): \x1b[13;2u → parsed by fnKeyRe, key.shift=true
      if ((key.meta && input === "\r") || (key.shift && key.return)) {
        // Insert newline at cursor
        setInputState((prev) => ({
          value: prev.value.slice(0, prev.cursor) + "\n" + prev.value.slice(prev.cursor),
          cursor: prev.cursor + 1,
        }));
      } else if (key.return) {
        if (inputState.value.trim()) {
          // Save to history (skip duplicates)
          const val = inputState.value;
          const hist = historyRef.current;
          if (hist.length === 0 || hist[hist.length - 1] !== val) {
            hist.push(val);
            // Keep max 100 entries
            if (hist.length > 100) hist.shift();
          }
          historyIndexRef.current = -1;
          draftRef.current = "";
          onSubmit(val);
          setInputState({ value: "", cursor: 0 });
        }
      } else if (key.upArrow) {
        const lines = inputState.value.split("\n");
        if (lines.length > 1) {
          // Multi-line: move cursor up one line
          const { line, col } = getCursorLineCol(inputState.value, inputState.cursor);
          if (line > 0) {
            const newCursor = cursorFromLineCol(inputState.value, line - 1, col);
            setInputState((prev) => ({ ...prev, cursor: newCursor }));
          }
        } else {
          // Single-line: navigate history backward (older)
          const hist = historyRef.current;
          if (hist.length === 0) return;
          if (historyIndexRef.current === -1) {
            draftRef.current = inputState.value;
            historyIndexRef.current = hist.length - 1;
          } else if (historyIndexRef.current > 0) {
            historyIndexRef.current -= 1;
          }
          const prev = hist[historyIndexRef.current];
          setInputState({ value: prev, cursor: prev.length });
        }
      } else if (key.downArrow) {
        const lines = inputState.value.split("\n");
        if (lines.length > 1) {
          // Multi-line: move cursor down one line
          const { line, col } = getCursorLineCol(inputState.value, inputState.cursor);
          if (line < lines.length - 1) {
            const newCursor = cursorFromLineCol(inputState.value, line + 1, col);
            setInputState((prev) => ({ ...prev, cursor: newCursor }));
          }
        } else {
          // Single-line: navigate history forward (newer)
          if (historyIndexRef.current === -1) return;
          const hist = historyRef.current;
          if (historyIndexRef.current < hist.length - 1) {
            historyIndexRef.current += 1;
            const next = hist[historyIndexRef.current];
            setInputState({ value: next, cursor: next.length });
          } else {
            historyIndexRef.current = -1;
            setInputState({ value: draftRef.current, cursor: draftRef.current.length });
          }
        }
      } else if (key.tab) {
        // Tab-complete slash commands
        if (inputState.value.startsWith("/")) {
          const matches = allCommandNames.filter((c) =>
            c.startsWith(inputState.value)
          );
          if (matches.length === 1) {
            setInputState({ value: matches[0], cursor: matches[0].length });
          } else if (matches.length > 1) {
            let prefix = matches[0];
            for (const m of matches.slice(1)) {
              while (!m.startsWith(prefix) && prefix.length > 0) {
                prefix = prefix.slice(0, -1);
              }
            }
            if (prefix.length > inputState.value.length) {
              setInputState({ value: prefix, cursor: prefix.length });
            }
          }
        }
      } else if (key.leftArrow) {
        setInputState((prev) => ({ ...prev, cursor: Math.max(0, prev.cursor - 1) }));
      } else if (key.rightArrow) {
        setInputState((prev) => ({ ...prev, cursor: Math.min(prev.value.length, prev.cursor + 1) }));
      } else if (key.backspace || key.delete) {
        // Ink maps most terminals' Backspace key (\x7f) to key.delete=true,
        // and Ctrl+H (\b) to key.backspace=true. Both should delete before cursor.
        // The real forward-Delete key (\x1b[3~) also maps to key.delete — but
        // Backspace is far more common, so we treat both as "delete before cursor".
        setInputState((prev) => {
          if (prev.cursor > 0) {
            return {
              value: prev.value.slice(0, prev.cursor - 1) + prev.value.slice(prev.cursor),
              cursor: prev.cursor - 1,
            };
          }
          return prev;
        });
      } else if (key.home) {
        setInputState((prev) => ({ ...prev, cursor: 0 }));
      } else if (key.end) {
        setInputState((prev) => ({ ...prev, cursor: prev.value.length }));
      } else if (input && !key.ctrl && !key.meta) {
        // Insert text at cursor position (input may be multi-char from paste)
        // Normalize \r\n to \n and strip standalone \r to avoid triggering submit
        const sanitized = input.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
        setInputState((prev) => ({
          value: prev.value.slice(0, prev.cursor) + sanitized + prev.value.slice(prev.cursor),
          cursor: prev.cursor + sanitized.length,
        }));
      }
    },
    { isActive: !disabled }
  );

  // Use a very long separator string — Ink's Yoga layout will truncate to terminal width.
  // This avoids needing React re-renders on resize (Ink handles layout recalc internally).
  const separator = "─".repeat(999);

  const { value, cursor } = inputState;

  return (
    <Box flexDirection="column">
      {/* Command suggestions */}
      {matchingCommands.length > 0 && (
        <Box flexDirection="column" paddingLeft={2} paddingBottom={0}>
          {matchingCommands.map((cmd) => {
            const info = commandDescMap.get(cmd);
            const isSkill = info?.source && info.source !== "builtin";
            return (
              <Box key={cmd}>
                <Text color={isSkill ? "cyan" : "green"} bold>
                  {cmd}
                </Text>
                <Text dimColor>
                  {"  "}
                  {info?.description || ""}
                </Text>
                {isSkill && (
                  <Text dimColor color="magenta">
                    {" "}
                    [{info.source}]
                  </Text>
                )}
              </Box>
            );
          })}
        </Box>
      )}
      {/* Input line */}
      <Box flexDirection="column">
        <Box width="100%"><Text color="gray" wrap="truncate-end">{separator}</Text></Box>
        {(() => {
          const lines = value.split("\n");
          const { line: cursorLine, col: cursorCol } = getCursorLineCol(value, cursor);

          return lines.map((line, i) => {
            const isCursorLine = i === cursorLine;
            const isFirstLine = i === 0;

            if (isCursorLine) {
              const leftPart = line.slice(0, cursorCol);
              const cursorChar = line[cursorCol] || " ";
              const rightPart = line.slice(cursorCol + 1);
              return (
                <Box key={i} paddingLeft={1} paddingRight={1}>
                  <Text color="gray" bold>{isFirstLine ? "nextcode " : "         "}</Text>
                  <Text color="gray">{isFirstLine ? "❯ " : "  "}</Text>
                  <Text>{leftPart}</Text>
                  {!disabled && <Text color={isQueryRunning ? "yellow" : "gray"} inverse>{cursorChar}</Text>}
                  <Text>{rightPart}</Text>
                  {disabled && <Text dimColor>{" waiting for permission..."}</Text>}
                  {isQueryRunning && !disabled && value.length === 0 && <Text dimColor color="yellow">{" type to queue a task..."}</Text>}
                </Box>
              );
            }

            return (
              <Box key={i} paddingLeft={1} paddingRight={1}>
                <Text color="gray" bold>{isFirstLine ? "nextcode " : "         "}</Text>
                <Text color="gray">{isFirstLine ? "❯ " : "  "}</Text>
                <Text>{line}</Text>
              </Box>
            );
          });
        })()}
        <Box width="100%"><Text color="gray" wrap="truncate-end">{separator}</Text></Box>
      </Box>
      {/* Status line: permission mode + context bar */}
      <Box paddingLeft={1}>
        <Text dimColor>
          {permissionMode === "acceptEdits"
            ? "⏵⏵ accept edits — auto allows working dir edits"
            : permissionMode === "plan"
              ? "📖 plan mode — read only, asks before writes"
              : permissionMode === "bypassPermissions"
                ? "⚡ bypass permissions — skip most prompts"
                : "⏹ default — asks before writes"}
          {" (shift+tab to cycle)"}
        </Text>
        {contextInfo && (() => {
          const { tokenUsage, effectiveWindow, warningState } = contextInfo;
          const usedPct = effectiveWindow > 0 ? Math.round((tokenUsage / effectiveWindow) * 100) : 0;
          const barLen = 10;
          const filled = effectiveWindow > 0 ? Math.min(barLen, Math.round(barLen * tokenUsage / effectiveWindow)) : 0;
          const bar = "█".repeat(filled) + "░".repeat(barLen - filled);
          const fmtK = (n: number) => {
            const k = Math.round(n / 1000);
            return `${k}k`;
          };
          const barColor = warningState === "blocking" ? "red" : (warningState === "auto_compact" || warningState === "warning") ? "yellow" : "green";
          return (
            <>
              <Text dimColor>{"｜Context "}</Text>
              <Text color={barColor}>{bar}</Text>
              <Text dimColor>{` ${fmtK(tokenUsage)}/${fmtK(effectiveWindow)} (${usedPct}%)`}</Text>
            </>
          );
        })()}
      </Box>
    </Box>
  );
}
