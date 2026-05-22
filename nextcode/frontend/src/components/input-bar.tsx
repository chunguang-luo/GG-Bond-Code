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

interface InputBarProps {
  inputState: InputState;
  setInputState: (value: InputState | ((prev: InputState) => InputState)) => void;
  onSubmit: (text: string) => void;
  disabled?: boolean;
  isQueryRunning?: boolean;
  model?: string;
  commands?: CommandInfo[];
  columns?: number;
}

export function InputBar({
  inputState,
  setInputState,
  onSubmit,
  disabled,
  isQueryRunning,
  model,
  commands = [],
  columns,
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

  useInput(
    (input, key) => {
      if (key.return) {
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
        // Navigate history backward (older)
        const hist = historyRef.current;
        if (hist.length === 0) return;
        if (historyIndexRef.current === -1) {
          // Save current draft before entering history
          draftRef.current = inputState.value;
          historyIndexRef.current = hist.length - 1;
        } else if (historyIndexRef.current > 0) {
          historyIndexRef.current -= 1;
        }
        const prev = hist[historyIndexRef.current];
        setInputState({ value: prev, cursor: prev.length });
      } else if (key.downArrow) {
        // Navigate history forward (newer)
        if (historyIndexRef.current === -1) return;
        const hist = historyRef.current;
        if (historyIndexRef.current < hist.length - 1) {
          historyIndexRef.current += 1;
          const next = hist[historyIndexRef.current];
          setInputState({ value: next, cursor: next.length });
        } else {
          // Back to the draft
          historyIndexRef.current = -1;
          setInputState({ value: draftRef.current, cursor: draftRef.current.length });
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
        setInputState((prev) => ({
          value: prev.value.slice(0, prev.cursor) + input + prev.value.slice(prev.cursor),
          cursor: prev.cursor + input.length,
        }));
      }
    },
    { isActive: !disabled }
  );

  const { value, cursor } = inputState;
  const leftOfCursor = value.slice(0, cursor);
  const rightOfCursor = value.slice(cursor);

  return (
    <Box flexDirection="column" width={columns}>
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
      <Box borderStyle="single" borderColor={isQueryRunning ? "yellow" : "gray"} paddingLeft={1} paddingRight={1}>
        <Text color="gray" bold>
          {"nextcode "}
        </Text>
        <Text color="gray">{"❯ "}</Text>
        <Text>{leftOfCursor}</Text>
        {!disabled && <Text color={isQueryRunning ? "yellow" : "gray"} inverse>{rightOfCursor.length > 0 ? rightOfCursor[0] : " "}</Text>}
        <Text>{rightOfCursor.length > 0 ? rightOfCursor.slice(1) : ""}</Text>
        {disabled && <Text dimColor>{" waiting for permission..."}</Text>}
        {isQueryRunning && !disabled && value.length === 0 && <Text dimColor color="yellow">{" type to queue a task..."}</Text>}
      </Box>
    </Box>
  );
}
