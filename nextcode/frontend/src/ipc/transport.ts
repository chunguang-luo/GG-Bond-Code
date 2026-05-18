/**
 * IPC Transport — Unix domain socket client for Ink ↔ Python communication.
 *
 * Connects to the Python Core's socket server and provides:
 * - JSON-line message framing (encode/decode)
 * - Bidirectional async send/receive
 * - Reconnection support (future)
 */

import * as net from "net";
import { Message } from "./protocol";

export class IPCTransport {
  private socket: net.Socket | null = null;
  private buffer = "";
  private messageHandler: ((msg: Message) => void) | null = null;
  private _connected = false;

  constructor(private socketPath: string) {}

  get connected(): boolean {
    return this._connected;
  }

  onMessage(handler: (msg: Message) => void): void {
    this.messageHandler = handler;
  }

  connect(timeout = 10000): Promise<void> {
    return new Promise((resolve, reject) => {
      const socket = new net.Socket();

      const timer = setTimeout(() => {
        socket.destroy();
        reject(new Error(`Connection timeout after ${timeout}ms`));
      }, timeout);

      socket.connect(this.socketPath, () => {
        clearTimeout(timer);
        this.socket = socket;
        this._connected = true;
        resolve();
      });

      socket.on("data", (data: Buffer) => {
        this.buffer += data.toString("utf-8");
        this._processBuffer();
      });

      socket.on("close", () => {
        this._connected = false;
      });

      socket.on("error", (err) => {
        clearTimeout(timer);
        this._connected = false;
        reject(err);
      });
    });
  }

  send(msg: Message): void {
    if (!this.socket || !this._connected) {
      throw new Error("Not connected");
    }
    const data: Record<string, unknown> = { type: msg.type, payload: msg.payload };
    if (msg.id) data.id = msg.id;
    const line = JSON.stringify(data) + "\n";
    this.socket.write(line, "utf-8");
  }

  sendEvent(type: string, payload: Record<string, unknown> = {}, id = ""): void {
    this.send({ type, payload, id });
  }

  close(): void {
    if (this.socket) {
      this.socket.destroy();
      this.socket = null;
      this._connected = false;
    }
  }

  private _processBuffer(): void {
    let idx: number;
    while ((idx = this.buffer.indexOf("\n")) !== -1) {
      const line = this.buffer.slice(0, idx).trim();
      this.buffer = this.buffer.slice(idx + 1);
      if (!line) continue;

      try {
        const msg = this._decode(line);
        if (this.messageHandler) {
          this.messageHandler(msg);
        }
      } catch (e) {
        process.stderr.write(`[IPC] Invalid message: ${e}\n`);
      }
    }
  }

  private _decode(line: string): Message {
    const data = JSON.parse(line);
    return {
      type: data.type,
      payload: data.payload || {},
      id: data.id || "",
    };
  }
}