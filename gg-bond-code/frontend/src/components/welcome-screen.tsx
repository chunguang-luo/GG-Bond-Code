/**
 * WelcomeScreen — ASCII art logo + tips panel.
 *
 * Mirrors the Rich REPL welcome screen from repl.py _print_welcome().
 */

import React from "react";
import { Box, Text } from "ink";

interface WelcomeScreenProps {
  model: string;
  cwd: string;
}

export function WelcomeScreen({ model, cwd }: WelcomeScreenProps) {
  // Shorten cwd for display
  const home = process.env.HOME || "";
  let displayCwd = cwd;
  if (home && cwd.startsWith(home)) {
    displayCwd = "~" + cwd.slice(home.length);
  }
  if (displayCwd.length > 40) {
    const parts = displayCwd.split("/");
    displayCwd = parts.length > 3 ? parts.slice(-3).join("/") : displayCwd;
  }

  return (
    <Box flexDirection="column" paddingX={1} paddingY={0}>
      <Box borderStyle="round" borderColor="blue" paddingX={1} paddingY={0}>
        <Box flexDirection="column">
          <Text bold color="magenta">
            {"   ^-----^"}
          </Text>
          <Text bold color="magenta">
            {"  ( o   o )"}
          </Text>
          <Text bold color="magenta">
            {" (   ( )   )"}
          </Text>
          <Text bold color="magenta">
            {"  \  ---  /"}
          </Text>
          <Text> </Text>
          <Text bold>  Welcome back!</Text>
        </Box>
        <Box flexDirection="column" marginLeft={4}>
          <Text> </Text>
          <Text> </Text>
          <Text> </Text>
          <Text> </Text>
          <Text bold> Tips for getting started</Text>
          <Text dimColor> Type /help for available commands</Text>
          <Text dimColor> Type /context to check token usage</Text>
          <Text dimColor> {" ─────────────────────────────────"}</Text>
          <Text>
            {" "}
            {model}  ·  {displayCwd}
          </Text>
        </Box>
      </Box>
    </Box>
  );
}