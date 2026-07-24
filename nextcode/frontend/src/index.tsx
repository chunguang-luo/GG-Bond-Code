/**
 * Ink Frontend Entry Point
 *
 * Reads IPC messages from inherited pipe fd (NEXTCODE_IPC_RX_FD),
 * writes IPC messages to inherited pipe fd (NEXTCODE_IPC_TX_FD).
 * Stdin/stdout are reserved for Ink terminal rendering.
 */

import React from "react";
import { render } from "ink";
import { App } from "./app";
import { IPCTransport } from "./ipc/transport";

// Parse CLI arguments (--session-id only; socket removed)
const args = process.argv.slice(2);
let sessionId = "default";

for (let i = 0; i < args.length; i++) {
  if (args[i] === "--session-id" && args[i + 1]) {
    sessionId = args[i + 1];
    i++;
  }
}

// Verify we have the inherited pipe fds
const rxFd = process.env.NEXTCODE_IPC_RX_FD;
const txFd = process.env.NEXTCODE_IPC_TX_FD;
if (!rxFd || !txFd) {
  process.stderr.write(
    "Error: NEXTCODE_IPC_RX_FD and NEXTCODE_IPC_TX_FD environment variables required.\n"
  );
  process.exit(1);
}

// Check if stdin supports raw mode (required by Ink)
if (!process.stdin.isTTY) {
  process.stderr.write("Error: Ink requires an interactive terminal (TTY stdin)\n");
  process.exit(1);
}

// Connect via pipe fds
const transport = new IPCTransport();
transport.connect();

// Send ready message
transport.sendEvent("ready", {
  version: "0.1.0",
  terminalInfo: {
    columns: process.stdout.columns,
    rows: process.stdout.rows,
    supportsTrueColor: true,
    supportsSyncOutput: false,
    termProgram: process.env.TERM_PROGRAM || "unknown",
  },
});

// Render Ink app
const { unmount, waitUntilExit } = render(
  React.createElement(App, { transport })
);

waitUntilExit()
  .then(() => {
    transport.close();
    process.exit(0);
  })
  .catch(() => {
    transport.close();
    process.exit(1);
  });

// Graceful shutdown on SIGTERM
process.on("SIGTERM", () => {
  transport.close();
  unmount();
});

// Handle SIGINT — send interrupt to Python
process.on("SIGINT", () => {
  try {
    transport.sendEvent("user.interrupt", {});
  } catch {
    // Disconnected — ignore
  }
});

// Ignore SIGPIPE
process.on("SIGPIPE", () => {});

// Global error handlers
process.on("uncaughtException", (err) => {
  process.stderr.write(`[NextCode] Uncaught exception: ${err.message}\n`);
  transport.close();
  process.exit(1);
});

process.on("unhandledRejection", (reason) => {
  process.stderr.write(`[NextCode] Unhandled rejection: ${reason}\n`);
});
