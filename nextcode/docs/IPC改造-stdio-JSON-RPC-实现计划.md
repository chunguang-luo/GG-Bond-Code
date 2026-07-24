# IPC 改造：Unix Domain Socket JSON-Line → stdio JSON-RPC 2.0

## Context

当前 Python 后端与 Node.js 前端通过 **Unix Domain Socket + JSON-Line** 通信：
- UDS 只在 macOS/Linux 可用，**Windows 不支持**，无法跨平台
- JSON-Line 是自定义 fire-and-forget 格式，无标准请求/响应关联

改为 **stdio + JSON-RPC 2.0**：
- stdio 所有 OS 通用，**一次改造即可跨平台**
- JSON-RPC 2.0 是成熟标准，支持 request/response/notification
- 可复用 MCP 模块中已有的 JSON-RPC 2.0 手写模式

## 核心挑战：IPC 通道与终端通道分离

当前架构中 Node.js(Ink) 的 stdin 既要读 JSON-Line IPC 数据，又要读用户键盘事件——但 stdin 只能读一次。当前通过 **UDS 旁路** 解决了这个问题：IPC 走 socket，stdin 留给 Ink。

改为 stdio 后，需要解决这个冲突。**方案**：Node.js 通过额外 fd 读取 JSON-RPC 数据，stdin 留给 Ink 键盘输入。

```
Python                                    Node.js (Ink)
  │                                         │
  ├─ subprocess.stdin  ──JSON-RPC──→  fd 3 (inherited pipe reads JSON-RPC)
  ├─ subprocess.stdout ←──JSON-RPC──  process.stdout
  │                                         │
  │                                    process.stdin (TTY, 留给 Ink useInput)
  │                                    process.stdout (Ink ANSI 渲染输出)
```

Python 端通过 `os.pipe()` 创建额外管道，`pass_fds` 传给子进程。Node.js 通过 `fs.createReadStream(null, {fd: 3})` 或 `net.Socket({fd: 3})` 读取。
- macOS/Linux：原生支持
- Windows：Python 3.13 的 `pass_fds` 在 Windows 上可用（`os.set_handle_inheritable`），Node.js 可访问继承的 fd

## 改造范围

### 1. Python 端

#### 1.1 `ipc/protocol.py` — JSON-RPC 2.0 消息格式
- 新增 `JsonRpcRequest` / `JsonRpcNotification` / `JsonRpcResponse` 数据类
- 序列化：请求 `{"jsonrpc":"2.0","id":N,"method":"...","params":{...}}`，通知无 `id`，响应有 `result`/`error`
- `CoreToInk` / `InkToCore` 枚举值作为 `method` 字段
- 保留 `Message` 类作为桥接（逐步弃用）

#### 1.2 `ipc/transport.py` — UDS Server → Subprocess stdio + pipe
- 删除 `asyncio.start_unix_server()` 和相关代码
- 构造函数接收 `asyncio.subprocess.Process` + IPC fd
- `send()` 写入 `process.stdin`（或 IPC pipe write end）
- 接收循环从额外 pipe fd 逐行读取
- 新增 stderr drain 任务
- `close()` 触发子进程关闭序列（SIGTERM → SIGKILL）

#### 1.3 `ipc/ink_launcher.py` — 进程启动改造
- `launch()` 创建 `os.pipe()` 作为 IPC 通道
- 将 pipe 读端 fd 通过 `pass_fds` 传给子进程
- 设置环境变量 `NEXTCODE_IPC_FD=3` 让 Node.js 知道从哪里读 IPC 数据
- 不再传 `--socket` 参数
- TTY/PTY 传递逻辑保持不变

#### 1.4 `main.py` — 启动流程调整
- `launcher.launch()` 返回 `(process, ipc_fd)`
- `transport.start(process, ipc_fd)`
- 移除 `wait_for_connection()`，进程启动即连接

### 2. Node.js 端

#### 2.1 `ipc/transport.ts` — net.Socket → fd stream
- 删除 `net.Socket` 连接逻辑
- 从 `NEXTCODE_IPC_FD` 环境变量获取 IPC fd
- 通过 `net.Socket({fd: ipcFd})` 或 readline 接口读取 JSON-RPC 行
- `send()` 写入 `process.stdout`
- `connect()` 同步初始化
- `close()` 退出自身进程

#### 2.2 `ipc/protocol.ts` — JSON-RPC 2.0 格式
- `Message` 改为 JSON-RPC 格式：`{jsonrpc, id?, method?, params?}`
- 新增 `sendRequest(method, params): Promise<response>` 等待响应
- 新增 `sendNotification(method, params)` fire-and-forget
- `sendEvent()` → `sendNotification()`（大部分消息）
- 权限请求等 → `sendRequest()`（需要响应）

#### 2.3 `index.tsx` — 入口简化
- 移除 `--socket` CLI 参数
- 移除连接超时/重试
- 启动即进入就绪状态

#### 2.4 `app.tsx` — 无需改动
- 消息 type 不变，switch-case 分派逻辑完全不变
- 只需 transport API 调用从 `sendEvent` 改为 `sendNotification`

## 消息流向对比

```
当前 (UDS JSON-Line)：
  Node → Python: {"type":"user.message","payload":{"text":"hello"}}\n
  Python → Node: {"type":"query.text_delta","payload":{"text":"Hi"}}\n

目标 (stdio JSON-RPC)：
  Node → Python: {"jsonrpc":"2.0","method":"user.message","params":{"text":"hello"}}\n
  Python → Node: {"jsonrpc":"2.0","method":"query.text_delta","params":{"text":"Hi"}}\n
```

## 改造文件清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `nextcode/src/next_code/ipc/protocol.py` | 重写 | JSON-RPC 2.0 消息定义+编解码 |
| `nextcode/src/next_code/ipc/transport.py` | 重写 | UDS → stdio+pipe 传输 |
| `nextcode/src/next_code/ipc/ink_launcher.py` | 修改 | 新增 pipe 创建+fd 传递 |
| `nextcode/src/next_code/main.py` | 小改 | 启动流程适配 |
| `nextcode/frontend/src/ipc/protocol.ts` | 修改 | JSON-RPC 2.0 格式 |
| `nextcode/frontend/src/ipc/transport.ts` | 重写 | Socket → fd stream |
| `nextcode/frontend/src/index.tsx` | 小改 | 移除 --socket 参数 |
| `nextcode/frontend/src/app.tsx` | 小改 | sendEvent → sendNotification |

## 验证方式

1. 构建前端：`cd nextcode/frontend && npm run build`
2. 启动 nextcode 交互模式：验证欢迎页面、消息收发、工具调用渲染
3. 发送消息验证流式输出正常
4. ESC 中断、权限弹窗、Agent 分组渲染
5. `/exit` 正常退出，进程清理无残留
6. （非 macOS 环境下验证）Linux 下运行确认 pipe 通信正常
