/**
 * PermissionDialog — interactive permission prompt.
 *
 * Shows tool name and params, allows user to:
 * - [y] Allow once
 * - [a] Always allow (session + persist)
 * - [n] Deny
 */

import React, { useCallback } from "react";
import { Box, Text, useInput } from "ink";

interface PermissionDialogProps {
  toolName: string;
  params: Record<string, unknown>;
  onResponse: (decision: "allow" | "deny" | "always_allow") => void;
}

export function PermissionDialog({ toolName, params, onResponse }: PermissionDialogProps) {
  useInput(
    (input, key) => {
      if (key.return) return;
      const ch = input.toLowerCase();
      if (ch === "y") {
        onResponse("allow");
      } else if (ch === "a") {
        onResponse("always_allow");
      } else if (ch === "n") {
        onResponse("deny");
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
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor="yellow"
      paddingX={2}
      paddingY={1}
    >
      <Box marginBottom={1}>
        <Text color="yellow" bold>
          ⚙ {toolName} wants to execute:
        </Text>
      </Box>
      {paramLines.map((line, i) => (
        <Box key={i}>
          <Text dimColor>{line}</Text>
        </Box>
      ))}
      <Box marginTop={1}>
        <Text bold>Allow? </Text>
        <Text color="green">[y]</Text>
        <Text>es </Text>
        <Text color="green">[a]</Text>
        <Text>ll </Text>
        <Text color="red">[n]</Text>
        <Text>o</Text>
      </Box>
    </Box>
  );
}
