/**
 * PermissionDialog — interactive permission prompt with inline input.
 *
 * Shows tool name and params, then an input box where the user types
 * y (allow), a (always allow), or n (deny) and presses Enter.
 */

import React, { useState } from "react";
import { Box, Text, useInput } from "ink";

interface PermissionDialogProps {
  toolName: string;
  params: Record<string, unknown>;
  onResponse: (decision: "allow" | "deny" | "always_allow") => void;
}

export function PermissionDialog({ toolName, params, onResponse }: PermissionDialogProps) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");

  useInput(
    (input, key) => {
      if (key.return) {
        const ch = value.trim().toLowerCase();
        if (ch === "y" || ch === "yes") {
          onResponse("allow");
        } else if (ch === "a" || ch === "always") {
          onResponse("always_allow");
        } else if (ch === "n" || ch === "no") {
          onResponse("deny");
        } else {
          setError("Enter y, n, or a");
        }
        return;
      }
      if (key.backspace || key.delete) {
        setValue((prev) => prev.slice(0, -1));
        setError("");
      } else if (input && !key.ctrl && !key.meta) {
        // Only accept y, n, a characters
        const ch = input.toLowerCase();
        if (ch === "y" || ch === "n" || ch === "a") {
          setValue(ch);
          setError("");
        }
      }
    },
    { isActive: true }
  );

  // Format params for display
  const paramLines = Object.entries(params).map(([k, v]) => {
    const val = String(v);
    const display = val.length > 100 ? val.slice(0, 97) + "..." : val;
    return `    ${k}: ${display}`;
  });

  return (
    <Box flexDirection="column">
      {/* Permission info */}
      <Box flexDirection="column" paddingLeft={2}>
        <Box>
          <Text color="red" bold>{"⚙ "}{toolName}</Text>
          <Text> wants to execute:</Text>
        </Box>
        {paramLines.map((line, i) => (
          <Box key={i}>
            <Text dimColor>{line}</Text>
          </Box>
        ))}
      </Box>
      {/* Inline input bar */}
      <Box paddingLeft={2}>
        <Text bold>Allow? </Text>
        <Text dimColor>[y]es [a]ll [n]o </Text>
        <Text color="red" bold>{"> "}</Text>
        <Text>{value}</Text>
        <Text inverse>{" "}</Text>
        {error && <Text color="red"> {error}</Text>}
      </Box>
    </Box>
  );
}
