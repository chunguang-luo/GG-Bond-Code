/**
 * useIPC — React hook for IPC message subscription.
 *
 * Provides:
 * - Automatic connection management
 * - Message dispatch to registered handlers
 * - Reconnection on disconnect (future)
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { IPCTransport } from "./transport";
import { Message, CoreToInk, InkToCore } from "./protocol";

export function useIPC(socketPath: string) {
  const transportRef = useRef<IPCTransport | null>(null);
  const [connected, setConnected] = useState(false);
  const handlersRef = useRef<Map<string, Set<(payload: Record<string, unknown>) => void>>>(
    new Map()
  );

  // Initialize transport
  useEffect(() => {
    const transport = new IPCTransport(socketPath);
    transportRef.current = transport;

    transport.onMessage((msg: Message) => {
      const handlers = handlersRef.current.get(msg.type);
      if (handlers) {
        for (const handler of handlers) {
          try {
            handler(msg.payload);
          } catch (e) {
            process.stderr.write(`[IPC] Handler error for ${msg.type}: ${e}\n`);
          }
        }
      }
    });

    transport
      .connect()
      .then(() => {
        setConnected(true);
        // Send ready message to Python Core
        transport.sendEvent(InkToCore.READY, {
          version: "0.1.0",
          terminalInfo: {
            columns: process.stdout.columns,
            rows: process.stdout.rows,
            supportsTrueColor: true, // TODO: detect
            supportsSyncOutput: false, // TODO: detect
            termProgram: process.env.TERM_PROGRAM || "unknown",
          },
        });
      })
      .catch((e: Error) => {
        process.stderr.write(`[IPC] Connection failed: ${e.message}\n`);
      });

    return () => {
      transport.close();
      transportRef.current = null;
      setConnected(false);
    };
  }, [socketPath]);

  // Subscribe to a specific message type
  const subscribe = useCallback(
    (type: string, handler: (payload: Record<string, unknown>) => void) => {
      if (!handlersRef.current.has(type)) {
        handlersRef.current.set(type, new Set());
      }
      handlersRef.current.get(type)!.add(handler);

      // Return unsubscribe function
      return () => {
        handlersRef.current.get(type)?.delete(handler);
      };
    },
    []
  );

  // Send a message to Python Core
  const send = useCallback((type: string, payload: Record<string, unknown> = {}) => {
    if (transportRef.current && transportRef.current.connected) {
      transportRef.current.sendEvent(type, payload);
    }
  }, []);

  return { connected, subscribe, send };
}