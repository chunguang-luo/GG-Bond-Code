/**
 * PermissionDialog — interactive permission prompt.
 *
 * Shows tool name and params, allows user to:
 * - [y] Allow once
 * - [a] Always allow (session + persist)
 * - [n] Deny
 */

import React, { useState } from "react";
import { Box, Text, useInput } from "ink";

interface PermissionDialogProps {
  toolName: string;
  params: Record<string, unknown>;
  onResponse: (decision: "allow" | "deny" | "always_allow") => void;
}

export function PermissionDialog({ toolName, params, onResponse }: PermissionDialogProps) {
  const [selected, setSelected] = useState<"y" | "a" | "n" | null>(null);

  useInput(
    (input, key) => {
      if (key.return) return;
      const ch = input.toLowerCase();
      if (ch === "y") {
        setSelected("y");
        onResponse("allow");
      } else if (ch === "a") {
        setSelected("a");
        onResponse("always_allow");
      } else if (ch === "n") {
        setSelected("n");
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
          {"⚙ "}{toolName} wants to execute:
        </Text>
      </Box>
      {paramLines.map((line, i) => (
        <Box key={i}>
          <Text dimColor>{line}</Text>
        </Box>
      ))}
      <Box marginTop={1}>
        <Text bold>Allow? </Text>
        <Text color={selected === "y" ? "green" : "gray"} bold={selected === "y"}>[y]es </Text>
        <Text color={selected === "a" ? "green" : "gray"} bold={selected === "a"}>[a]ll </Text>
        <Text color={selected === "n" ? "red" : "gray"} bold={selected === "n"}>[n]o</Text>
      </Box>
    </Box>
  );
}
