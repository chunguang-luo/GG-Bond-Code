# Ink Frontend 实现方案：Python Core + Ink 双进程架构

## Context

当前 GG Bond Code v0.3.0 是单进程 Python 应用，使用 Rich 做 Terminal UI。Rich 在以下方面存在根本性限制：
- **流式输出**：Live 渲染在大量输出时卡顿/闪烁，无虚拟滚动
- **交互能力**：不支持鼠标点击、文本选择、拖拽等交互
- **UI 精度**：无法实现 60fps 渲染、IME 输入、Alt Screen 模式
- **设计系统**：无主题系统、组件库、设计一致性

目标：引入 Node.js + Ink (React for CLI) 作为独立 UI 进程，与 Python Core 通过 IPC 通信，实现原版 Claude Code 级别的终端 UI 体验。

---

## 架构总览

```
┌─────────────────────────────────┐     Unix Socket      ┌──────────────────────────┐
│       Python Core (Parent)       │◄──── JSON-line ────►│   Node.js/Ink (Child)     │
│                                  │                      │                            │
│  ┌──────────┐  ┌──────────────┐  │                      │  ┌──────────────────────┐ │
│  │ QueryRun │  │ IPC Bridge   │  │  query.text_delta   │  │ React Components     │ │
│  │ ner      │──│              │──│──permission.request──│──│  REPL, MessageList,  │ │
│  │          │  │ Transport    │  │  state.update       │  │  InputBar, Dialog... │ │
│  │ Store    │  │ Protocol     │  │  ping               │  │                      │ │
│  │ Permissi │  │ InkLauncher  │◄─│──user.message       │◄─│  useIPC hook         │ │
│  │ onMgr    │  │ Fallback     │  │──permission.response│──│  useQueryEvents      │ │
│  └──────────┘  └──────────────┘  │──pong               │  └──────────────────────┘ │
│                                  │                      │  Design System            │
│  API Client, Tools, Compact...   │                      │  Theme, ScrollBox, Ratchet│
└─────────────────────────────────┘                      └──────────────────────────┘
```

**核心原则**：Python 是唯一的状态源，Ink 是纯渲染+输入层。

---

## 1. IPC 通信机制

### 选型：Unix Domain Socket + JSON-line 协议

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| stdin/stdout JSON | 简单 | 单向，与终端 I/O 冲突 | 淘汰 |
| **Unix Socket** | 双向，低延迟，无端口管理 | Windows 需 named pipe | **选用** |
| WebSocket | 调试方便 | TCP 开销，端口冲突 | 淘汰 |
| gRPC/protobuf | 强类型 | 依赖重，杀鸡用牛刀 | 淘汰 |

- Socket 路径：`/tmp/ggbond-ipc-<pid>.sock`
- Windows 回退：`\\.\pipe\ggbond-<pid>`
- 延迟：Unix socket < 1ms，远低于 60fps 的 16ms 预算

### 线格式

```json
{"type": "event_name", "id": "correlation-id?", "payload": {...}}\n
```

---

## 2. 进程生命周期

```
Python (Parent)                           Ink (Child)
─────────────                             ───────────
1. init() + setup()
2. 创建 Socket Server
3. spawn: node dist/index.js
   --socket /tmp/ggbond-ipc-<pid>.sock
   --session-id <uuid>
4. 等待 "ready" 消息 (5s 超时)           1. 解析参数
5. 发送 "session.ready"                   2. 连接 Socket
6. 进入消息循环                           3. 初始化 Ink App
                                          4. 发送 "ready"
                                          5. 进入消息循环

崩溃检测：                                优雅关闭：
- poll() 检测子进程退出                    - 发送 shutdown 消息
- 自动回退到 Rich REPL                    - 等待 2s → SIGTERM → SIGKILL

心跳：每 5s ping，10s 无响应判定挂起
Ctrl+C：SIGINT 同时发给两进程，Ink 转发 interrupt 消息给 Python
```

---

## 3. 消息协议

### Python → Ink (Core → UI)

| 类型 | 用途 | Payload 关键字段 |
|------|------|-----------------|
| `session.ready` | 会话就绪 | model, cwd, projectRoot |
| `session.shutdown` | 关闭通知 | reason |
| `query.text_delta` | 流式文本 | text |
| `query.thinking_delta` | 思考过程 | text |
| `query.tool_start` | 工具开始 | toolUseId, toolName |
| `query.tool_use` | 工具调用 | toolUseId, toolName, toolInput, toolPurpose |
| `query.tool_result` | 工具结果 | toolUseId, toolResult, toolError, elapsedMs |
| `query.error` | 错误 | content |
| `query.warning` | 上下文警告 | content, metadata{level, percentUsed, tokenUsage} |
| `query.complete` | 查询完成 | transitionReason, turnCount |
| `state.update` | 状态增量更新 | key, value, oldValue |
| `state.snapshot` | 状态全量快照 | model, cwd, messages[], ui{} |
| `permission.request` | 权限请求 | requestId, toolName, params |
| `context.info` | 上下文窗口信息 | model, contextWindow, tokenUsage, warningState |
| `welcome` | 欢迎屏数据 | model, cwd, asciiArt |
| `compact.started/complete` | 压缩通知 | level / reason |
| `ping` | 心跳 | timestamp |

### Ink → Python (UI → Core)

| 类型 | 用途 | Payload 关键字段 |
|------|------|-----------------|
| `ready` | 前端就绪 | version, terminalInfo{columns, rows, supportsTrueColor} |
| `pong` | 心跳响应 | timestamp |
| `user.message` | 用户输入 | text |
| `user.interrupt` | 中断请求 | - |
| `user.command` | 斜杠命令 | command |
| `permission.response` | 权限响应 | requestId, decision(allow/deny/always_allow), wildcard |
| `ui.toggle_thinking` | 切换思考显示 | enabled |
| `ui.resize` | 窗口大小变化 | columns, rows |
| `theme.change` | 主题切换 | theme |
| `shutdown.ack` | 关闭确认 | - |

### 权限请求-响应模式

```
QueryRunner._check_permission() → ASK
  → IPCBridge 发送 permission.request (带 requestId)
  → Ink 渲染 PermissionDialog
  → 用户选择 Allow/Deny/Always
  → Ink 发送 permission.response (相同 requestId)
  → IPCBridge resolve 等待中的 Future
  → QueryRunner 继续执行
```

超时 30s 无响应默认 DENY，Ink 崩溃时也安全回退。

---

## 4. Python 端新增模块

```
src/gg_bond_code/ipc/
    __init__.py
    transport.py      # Socket Server：创建、监听、消息帧、JSON 编解码
    protocol.py       # 消息类型定义、验证
    bridge.py         # IPCBridge：QueryEvent → IPC 消息适配器
    ink_launcher.py   # Ink 进程管理：spawn、健康检测、关闭
    fallback.py       # 回退检测：Node.js 不可用时切换到 Rich REPL
```

### 关键修改

| 文件 | 修改内容 |
|------|---------|
| `main.py` | 新增 `--ink` 参数（off/auto/on），添加 `_run_with_ink()` 路径 |
| `state/store.py` | 接入 `IPCStoreListener`，Store 变更自动推送到 Ink |
| `query.py` | 无修改，QueryEvent 已经是纯数据事件 |

### IPCBridge 核心逻辑

```python
class IPCBridge:
    """将 QueryRunner 事件适配为 IPC 消息"""

    async def run_query(self, user_message: str) -> None:
        async for event in self.runner.run(user_message):
            msg_type = f"query.{event.type}"  # text → query.text_delta 等
            await self.transport.send(msg_type, event_to_payload(event))

    async def ask_permission(self, tool_name, params) -> PermissionDecision:
        request_id = uuid4().hex
        future = asyncio.get_event_loop().create_future()
        self._pending_permissions[request_id] = future
        await self.transport.send("permission.request", {..., "requestId": request_id})
        return await asyncio.wait_for(future, timeout=30.0)  # 超时默认 DENY
```

---

## 5. Ink 前端项目结构

```
gg-bond-code/frontend/
    package.json              # ink, react, typescript 依赖
    tsconfig.json
    src/
        index.tsx             # 入口：解析参数、连接 Socket、render <App />
        app.tsx               # 根组件：<ThemeProvider><REPL /></ThemeProvider>

        ipc/
            transport.ts      # Socket 客户端、消息帧
            protocol.ts       # 消息类型定义（与 Python 端共享 schema）
            useIPC.ts         # React hook：订阅 IPC 消息

        components/
            repl.tsx          # 主 REPL 组件
            message-list.tsx  # 可滚动消息列表（ScrollBox）
            message-item.tsx  # 单条消息渲染（文本/工具/结果）
            input-bar.tsx     # 用户输入（IME 支持）
            permission-dialog.tsx  # 权限确认弹窗
            welcome-screen.tsx     # 欢迎面板
            context-bar.tsx   # 上下文窗口信息条
            command-bar.tsx   # 斜杠命令处理

        design-system/
            theme.ts          # 6 主题定义
            themed-text.tsx   # ThemedText 组件
            themed-box.tsx    # ThemedBox 组件
            pane.tsx / divider.tsx / dialog.tsx
            progress-bar.tsx  # 上下文用量进度条
            ratchet.tsx       # 流式输出防抖动组件

        hooks/
            use-query-events.ts   # 处理查询事件 → 渲染状态
            use-permission.ts     # 权限请求/响应
            use-terminal.ts       # 终端大小、能力检测
            use-scroll.ts         # ScrollBox 滚动管理

        utils/
            tool-formatter.ts # 工具调用/结果格式化（移植自 repl.py）
            markdown.ts       # Markdown 渲染
            terminal.ts       # 终端能力检测
    dist/                      # 构建产物（随 Python 包发布）
```

---

## 6. 构建与分发

**策略：预构建 bundle 随 Python 包发布**

- `frontend/` 是独立 npm 项目
- `npm run build` → `frontend/dist/index.js`（单文件 bundle）
- bundle 提交到 git / 作为 release 流程构建
- `pyproject.toml` 包含 `frontend/dist/` 到 package data
- `ink_launcher.py` 通过 `importlib.resources` 定位 bundle
- 用户无需安装 Node.js，除非要开发前端

**Node.js 版本要求**：≥ 18（仅 Ink 模式需要）

---

## 7. 分阶段迁移计划

### Phase 1：IPC 基础设施（无 UI 变化）

- 创建 `ipc/` 模块：transport.py、protocol.py、ink_launcher.py
- CLI 添加 `--ink` 参数（默认 off）
- 创建最小 `frontend/` 骨架（仅连接 + 发送 ready）
- 单元测试：Socket 创建、消息帧、JSON 编解码

### Phase 2：基础 Ink REPL（仅流式文本）

- 创建 `bridge.py`（QueryEvent → IPC 消息）
- 实现 Ink 组件：app.tsx、repl.tsx、message-list.tsx、input-bar.tsx
- 接通用户输入 → `user.message` → Python
- 接通流式文本 → `query.text_delta` → Ink 渲染

### Phase 3：工具调用 + 权限 UI

- 实现 `permission-dialog.tsx`
- 接通权限流：permission.request → Dialog → permission.response
- 实现 tool_use / tool_result 渲染
- 工具结果折叠/展开

### Phase 4：斜杠命令 + 完整功能

- 移植所有斜杠命令到 Ink
- 实现欢迎屏、上下文信息条、Thinking 切换
- Store 状态同步（state.update + state.snapshot）

### Phase 5：高级 Ink 特性

- ScrollBox 虚拟滚动
- 鼠标交互（滚动、点击、文本选择）
- 6 主题系统
- 终端能力检测（DA1、DEC 2026 同步输出）
- IME 输入支持
- 60fps 双缓冲渲染
- Ratchet 防抖动组件

### Phase 6：Ink 默认化

- `--ink` 默认值改为 `auto`
- 完善回退检测：node 未安装、bundle 缺失、连接超时、Ink 崩溃
- 心跳监控
- 更新文档

---

## 8. 关键技术决策

| 决策 | 选择 | 理由 |
|------|------|------|
| IPC 机制 | Unix Socket + JSON-line | 低延迟、双向、简单、易调试 |
| Python 为父进程 | 是 | Python 持有 API Key、文件系统访问、工具执行权限 |
| 终端归属 | Ink 独占 Alt Screen | 避免双进程同时写终端的冲突 |
| 官方 Ink vs Fork | Phase 1-4 官方，Phase 5 Fork | 渐进式，先用官方 Ink 验证基本流程 |
| 消息顺序 | Socket FIFO 保证 | 无需额外序列号 |
| 状态同步 | 增量事件 + 周期快照 | 低延迟 + 容错 |

---

## 9. 风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| Node.js 未安装 | 无法启动 Ink | 自动回退 Rich REPL，零感知 |
| Ink 崩溃（查询中） | 权限 Future 挂起 | 30s 超时默认 DENY |
| 流式延迟增加 | 输出卡顿 | Unix socket <1ms，16ms 预算充裕 |
| 终端兼容性 | 特性缺失 | 检测能力 + 优雅降级 |
| 大状态快照 | 消息过大 | 消息已通过 query 事件增量传输，快照仅作安全网 |

---

## 10. 验证方案

### Phase 1 验证
- `ggbond --ink=off` 行为无回归
- `ggbond --ink=auto`（无 Node.js）自动回退到 Rich REPL
- Socket 连接/断开/重连 单元测试通过

### Phase 2 验证
- `ggbond --ink=auto` 启动 Ink 前端
- 输入问题，流式文本正常显示
- Ctrl+C 中断查询正常工作

### Phase 3 验证
- Bash 工具触发权限弹窗，Allow/Deny/Always 功能正常
- "Always Allow" 持久化到 .settings.json

### Phase 4 验证
- 所有斜杠命令功能与 Rich REPL 一致
- /compact、/thinking、/model 等命令正常

### Phase 5 验证
- 1000+ 条消息历史滚动流畅
- 鼠标滚动、点击在 iTerm2/Ghostty/Terminal.app 正常

### Phase 6 验证
- 无 Node.js 环境下 `ggbond` 自动使用 Rich REPL
- 有 Node.js 环境下 `ggbond` 自动使用 Ink 前端