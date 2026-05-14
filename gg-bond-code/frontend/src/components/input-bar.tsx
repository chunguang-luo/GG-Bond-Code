/**
 * InputBar — user input component with prompt.
 *
 * Handles text input and Enter key to submit messages.
 * In Phase 5, this will get IME input support.
 */

import React, { useCallback } from "react";
import { Box, Text, useInput } from "ink";

interface InputBarProps {
  inputValue: string;
  setInputValue: (value: string) => void;
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
        {"ggbond "}
      </Text>
      <Text color="gray">{"❯ "}</Text>
      <Text>{inputValue}</Text>
      {!disabled && <Text backgroundColor="green">{" "}</Text>}
      {disabled && (
        <Text dimColor> thinking...</Text>
      )}
    </Box>
  );
}
