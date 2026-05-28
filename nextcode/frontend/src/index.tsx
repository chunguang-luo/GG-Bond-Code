/**
 * Ink Frontend Entry Point
 *
 * Parses CLI arguments (--socket, --session-id),
 * connects to the Python Core via IPC,
 * and renders the Ink React app.
 */

import React from "react";
import { render } from "ink";
import { App } from "./app";
import { IPCTransport } from "./ipc/transport";

// Parse CLI arguments
const args = process.argv.slice(2);
let socketPath = "";

for (let i = 0; i < args.length; i++) {
  if (args[i] === "--socket" && args[i + 1]) {
    socketPath = args[i + 1];
    i++;
  }
}

if (!socketPath) {
  process.stderr.write("Error: --socket argument required\n");
  process.exit(1);
}

// Check if stdin supports raw mode (required by Ink)
if (!process.stdin.isTTY) {
  process.stderr.write("Error: Ink requires an interactive terminal (TTY stdin)\n");
  process.stderr.write("Make sure stdin is connected to a terminal.\n");
  process.exit(1);
}

// Connect to Python Core and render
const transport = new IPCTransport(socketPath);

transport
  .connect()
  .then(() => {
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

    // Handle cleanup on exit (both normal and error unmount)
    waitUntilExit()
      .then(() => {
        transport.close();
        process.exit(0);
      })
      .catch(() => {
        transport.close();
        process.exit(1);
      });

    // Graceful shutdown on SIGTERM — let Ink unmount cleanly
    process.on("SIGTERM", () => {
      transport.close();
      unmount();
    });
  })
  .catch((e: Error) => {
    process.stderr.write(`Failed to connect to Python Core: ${e.message}\n`);
    process.exit(1);
  });

// Handle signals
process.on("SIGINT", () => {
  try {
    transport.sendEvent("user.interrupt", {});
  } catch {
    // Disconnected — ignore
  }
});

// Ignore SIGPIPE — prevents crash when stdout pipe breaks
process.on("SIGPIPE", () => {});

// Global error handlers — prevent uncaught exceptions from crashing the process
process.on("uncaughtException", (err) => {
  process.stderr.write(`[NextCode] Uncaught exception: ${err.message}\n`);
  transport.close();
  process.exit(1);
});

process.on("unhandledRejection", (reason) => {
  process.stderr.write(`[NextCode] Unhandled rejection: ${reason}\n`);
  // Don't exit on unhandled rejection — just log it
  // This prevents crash from race conditions in async code
});