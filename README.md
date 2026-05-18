# NextCode

用 Python 重新实现 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI 的核心主流程，保留架构精髓，去掉企业级复杂度。

## 特性

- **Ink 终端 UI** — React + Ink 前端，双进程 IPC 架构，Python 后端 + Node.js 前端，Unix Domain Socket 双向 JSON-Line 通信
- **多模型支持** — DeepSeek / Claude / MiniMax，自动选择 API 后端
- **核心工具集** — Bash、FileRead、FileEdit、FileWrite、Glob、Grep
- **流式工具执行** — 并发分区执行 + 流式输出
- **上下文压缩** — 三级压缩（MICRO / FULL / BLOCKING）+ 断路器保护
- **命令系统** — `/help`、`/clear`、`/compact`、`/context`、`/thinking`、`/model`、`/log`、`/exit`，支持 Tab 补全和相似命令提示
- **权限系统** — 工具执行 allow/deny 控制，交互式确认，通配符授权持久化
- **Prompt Cache** — 静态/动态 System Prompt 分割 + 工具 Schema 缓存
- **API 韧性** — 指数退避重试 + 错误恢复策略 + 超时保护
- **Thinking 展示** — 实时流式显示思考过程，`/thinking` 命令切换
- **分层配置** — 全局 + 项目级配置，环境变量覆盖

## 快速开始

### 安装

```bash
cd nextcode
pip install -e .
```

Ink 前端：

```bash
cd nextcode/frontend
npm install
npm run build
```

### 配置 API Key

```bash
# DeepSeek 模型
export NEXTCODE_API_KEY=your-deepseek-api-key

# Claude 模型
export ANTHROPIC_API_KEY=your-anthropic-api-key

# 或交互式配置
nextcode auth
```

### 使用

```bash
# 交互模式
nextcode

# 非交互模式
echo "解释什么是 Python 装饰器" | nextcode --print

# 指定模型
nextcode --model deepseek-reasoner
nextcode --model claude-sonnet-4-20250514
```

## 技术栈

| 层级 | 选型 |
|------|------|
| **后端语言** | Python 3.12+ |
| **CLI 框架** | Click |
| **终端 UI** | React 18 + Ink 5 |
| **Schema 验证** | Pydantic |
| **API 客户端** | anthropic SDK / openai SDK |
| **异步** | asyncio |
| **IPC** | Unix Domain Socket (JSON-Line) |
| **配置格式** | JSON |

## 项目结构

```
nextcode/
├── pyproject.toml
├── frontend/                     # Ink 终端 UI (React + Ink)
│   ├── src/
│   │   ├── index.tsx             # 入口
│   │   ├── app.tsx               # 根组件 + 状态管理
│   │   ├── components/           # UI 组件
│   │   ├── ipc/                   # IPC 传输层
│   │   ├── hooks/                 # React hooks
│   │   └── utils/                 # Markdown 渲染等工具
│   └── package.json
└── src/next_code/
    ├── cli.py                    # 入口 + 快速路径
    ├── main.py                   # Click 命令编排
    ├── init.py                   # 配置/认证/预连接初始化
    ├── setup.py                  # 会话级初始化
    ├── repl.py                   # REPL 循环
    ├── query.py                  # 对话循环核心
    ├── prefetch.py               # 系统上下文预取
    ├── config/                   # 配置 + 认证
    ├── api/                      # API 客户端（双后端 + 重试 + 恢复 + Prompt Cache）
    ├── tools/                    # 工具集 + 流式执行器
    ├── commands/                 # 命令系统（注册/调度/补全）
    ├── compact/                  # 上下文压缩（MICRO/FULL/BLOCKING）
    ├── permissions/              # 权限管理
    ├── prompts/                  # System Prompt 组装
    ├── state/                    # 状态管理（Store + ToolUseContext）
    ├── ipc/                      # IPC 桥接（Python ↔ Ink）
    ├── context/                  # 系统/用户上下文
    ├── agents/                   # 多 Agent 协调（规划中）
    └── memory/                   # Memory 系统（规划中）
```

## 架构概览

### 双进程 IPC 架构

```
                    ┌─────────────┐
                    │  CLI 入口    │
                    └──────┬──────┘
                           │
                ┌──────────┴──────────┐
                │   Ink Frontend     │
                │  Python + Node.js  │
                │   双进程 IPC 通信    │
                └─────────────────────┘
```

### IPC 消息流

```
Python 后端 (IPCBridge)              Ink 前端 (App)
       │                                    │
       ├── session.ready ──────────────────>│
       ├── welcome ─────────────────────────>│
       │                                    │
       │<── user.message ───────────────────│
       │<── user.command ───────────────────│
       │                                    │
       ├── query.text_delta ──────────────->│
       ├── query.thinking_delta ───────────>│
       ├── query.tool_start ───────────────>│
       ├── query.tool_use ─────────────────>│
       ├── query.tool_result ──────────────>│
       ├── query.complete ─────────────────>│
       │                                    │
       ├── permission.request ─────────────>│
       │<── permission.response ────────────│
       │                                    │
       ├── compact.started ────────────────>│
       ├── compact.complete ────────────────>│
       └── context.info ───────────────────>│
```

## 实现状态

| 功能 | 状态 |
|------|------|
| CLI 入口 + 快速路径 | ✅ |
| 分层启动链路 | ✅ |
| 配置系统 | ✅ |
| 多模型支持 | ✅ |
| 交互式 REPL (Ink) | ✅ |
| Ink 终端 UI | ✅ |
| 双进程 IPC 架构 | ✅ |
| 斜杠命令系统（注册/调度/补全/提示） | ✅ |
| 对话循环 + 工具执行 | ✅ |
| 流式输出 | ✅ |
| 流式工具执行（并发分区） | ✅ |
| 权限系统 + 持久化 | ✅ |
| 上下文压缩（MICRO/FULL/BLOCKING） | ✅ |
| Prompt Cache | ✅ |
| API 重试 + 超时 + 恢复策略 | ✅ |
| 工具安全执行 | ✅ |
| Thinking 展示 | ✅ |
| 状态管理（Store + ToolUseContext） | ✅ |
| 单元测试 | ✅ |
| 多 Agent 协调 | 📋 |
| Memory 系统 | 📋 |

## 文档

- [需求设计文档](nextcode/需求设计文档.md)
- [项目架构文档](nextcode/项目架构文档.md)
- [使用文档](nextcode/使用文档.md)
- [CHANGELOG](nextcode/CHANGELOG.md)
- [实现计划](nextcode/docs/) — 上下文管理、System Prompt、对话循环、命令系统等

## License

[MIT](LICENSE)
