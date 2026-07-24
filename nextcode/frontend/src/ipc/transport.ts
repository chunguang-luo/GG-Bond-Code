/**
 * IPC Transport — stdio pipe client for Ink ↔ Python communication.
 *
 * Reads JSON-RPC notifications from fd 3 (inherited pipe from Python),
 * writes JSON-RPC notifications to fd 4 (inherited pipe to Python).
 *
 * Stdin is reserved for Ink keyboard input; stdout for Ink rendering.
 */

import * as fs from "fs";
import * as readline from "readline";
import { Message } from "./protocol";

export class IPCTransport {
  private rxStream: fs.ReadStream | null = null;
  private rl: readline.Interface | null = null;
  private txStream: fs.WriteStream | null = null;
  private messageHandler: ((msg: Message) => void) | null = null;
  private _disconnectHandler: (() => void) | null = null;
  private _connected = false;

  get connected(): boolean {
    return this._connected;
  }

  onMessage(handler: (msg: Message) => void): void {
    this.messageHandler = handler;
  }

  onDisconnect(handler: () => void): void {
    this._disconnectHandler = handler;
  }

  connect(): void {
    const rxFd = parseInt(process.env.NEXTCODE_IPC_RX_FD || "3", 10);
    const txFd = parseInt(process.env.NEXTCODE_IPC_TX_FD || "4", 10);

    // Write stream for sending IPC messages to Python (fd = pipe write end)
    this.txStream = fs.createWriteStream("", { fd: txFd, autoClose: false });

    // Read stream for receiving IPC messages from Python (fd = pipe read end)
    this.rxStream = fs.createReadStream("", { fd: rxFd, autoClose: false });

    this.rl = readline.createInterface({
      input: this.rxStream,
      crlfDelay: Infinity,
    });

    this.rl.on("line", (line: string) => {
      if (!line.trim()) return;
      try {
        const raw = JSON.parse(line);
        const msg: Message = {
          type: raw.method || raw.type || "",
          payload: raw.params || raw.payload || {},
          id: raw.id !== undefined ? String(raw.id) : "",
        };
        if (this.messageHandler) {
          this.messageHandler(msg);
        }
      } catch {
        process.stderr.write(`[IPC] Invalid message: ${line.slice(0, 120)}\n`);
      }
    });

    this.rxStream.on("close", () => {
      this._connected = false;
      if (this._disconnectHandler) {
        this._disconnectHandler();
      }
    });

    this.rxStream.on("error", () => {
      this._connected = false;
      if (this._disconnectHandler) {
        this._disconnectHandler();
      }
    });

    this._connected = true;
  }

  send(msg: Message): void {
    if (!this.txStream || !this._connected) {
      return;
    }
    const line =
      JSON.stringify({
        jsonrpc: "2.0",
        method: msg.type,
        params: msg.payload,
      }) + "\n";
    try {
      this.txStream.write(line, "utf-8");
    } catch {
      this._connected = false;
    }
  }

  sendEvent(type: string, payload: Record<string, unknown> = {}, id = ""): void {
    this.send({ type, payload, id });
  }

  sendNotification(method: string, params: Record<string, unknown> = {}): void {
    this.send({ type: method, payload: params });
  }

  close(): void {
    if (this.rl) {
      this.rl.close();
      this.rl = null;
    }
    if (this.rxStream) {
      this.rxStream.destroy();
      this.rxStream = null;
    }
    if (this.txStream) {
      try {
        this.txStream.end();
      } catch {
        // Ignore close errors
      }
      this.txStream = null;
    }
    this._connected = false;
  }
}
