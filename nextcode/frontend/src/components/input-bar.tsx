/**
 * InputBar — user input component with prompt.
 *
 * Handles text input, Enter to submit, Tab to complete slash commands.
 */

import React from "react";
import { Box, Text, useInput } from "ink";

const SLASH_COMMANDS = [
  "/help",
  "/clear",
  "/compact",
  "/context",
  "/thinking",
  "/model",
  "/log",
  "/exit",
  "/quit",
];

interface InputBarProps {
  inputValue: string;
  setInputValue: (value: string | ((prev: string) => string)) => void;
  onSubmit: (text: string) => void;
  disabled?: boolean;
  model?: string;
}

export function InputBar({ inputValue, setInputValue, onSubmit, disabled, model }: InputBarProps) {
  useInput(
    (input, key) => {
      if (key.return) {
        if (inputValue.trim()) {
          onSubmit(inputValue);
          setInputValue("");
        }
      } else if (key.tab) {
        // Tab-complete slash commands
        if (inputValue.startsWith("/")) {
          const matches = SLASH_COMMANDS.filter((c) => c.startsWith(inputValue));
          if (matches.length === 1) {
            setInputValue(matches[0]);
          } else if (matches.length > 1) {
            // Find common prefix
            let prefix = matches[0];
            for (const m of matches.slice(1)) {
              while (!m.startsWith(prefix) && prefix.length > 0) {
                prefix = prefix.slice(0, -1);
              }
            }
            if (prefix.length > inputValue.length) {
              setInputValue(prefix);
            }
          }
        }
      } else if (key.backspace || key.delete) {
        setInputValue((prev) => prev.slice(0, -1));
      } else if (input && !key.ctrl && !key.meta) {
        setInputValue((prev) => prev + input);
      }
    },
    { isActive: !disabled }
  );

  return (
    <Box borderStyle="single" borderColor="green" paddingLeft={1} paddingRight={1}>
      <Text color="green" bold>
        {"nextcode "}
      </Text>
      <Text color="gray">{"❯ "}</Text>
      <Text>{inputValue}</Text>
      {!disabled && <Text color="green">{"▎"}</Text>}
      {disabled && (
        <Text dimColor>{" thinking..."}</Text>
      )}
    </Box>
  );
}
