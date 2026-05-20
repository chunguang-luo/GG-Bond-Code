# NextCode

用 Python 重新实现 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI 的核心主流程，保留架构精髓，去掉企业级复杂度。

[![Python 版本](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![代码风格](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

## 特性

- **Ink 终端 UI** — React + Ink 前端，双进程 IPC 架构，Python 后端 + Node.js 前端，Unix Domain Socket 双向 JSON-Line 通信
- **多模型支持** — DeepSeek / Claude / MiniMax，自动选择 API 后端
- **Agent 系统** — 内置 Explore/Plan/General-Purpose Agent，支持自定义 Agent，工具过滤、上下文隔离
- **Skill 系统** — Markdown → PromptCommand 转换，前置条件激活，懒惰加载
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

Ink 前端（可选）：

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

# 指定工作目录
nextcode --cwd /path/to/project

# 查看配置
nextcode config
```

## 内置命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助信息和可用命令列表 |
| `/clear` | 清空对话历史 |
| `/compact` | 压缩对话历史（模型摘要 + 保留近期消息） |
| `/context` | 显示上下文窗口使用情况 |
| `/thinking` | 切换 Thinking 内容显示 |
| `/model` | 显示当前使用的模型 |
| `/log` | 显示对话循环状态日志 |
| `/exit` `/quit` | 退出 REPL |

> 输入 `/` 后按 Tab 键可补全命令。输入错误命令时会提示相似命令。

## 可用工具

| 工具 | 说明 | 默认权限 |
|------|------|----------|
| `Read` | 读取文件内容 | 允许 |
| `Glob` | 按模式查找文件 | 允许 |
| `Grep` | 搜索文件内容 | 允许 |
| `Bash` | 执行 shell 命令 | 需确认 |
| `Edit` | 编辑文件（字符串替换） | 需确认 |
| `Write` | 写入文件 | 需确认 |
| `Agent` | 调用子 Agent 执行复杂任务 | 需确认 |

## Agent 系统

### 内置 Agent

| Agent | 说明 | 禁用工具 |
|-------|------|----------|
| `Explore` | 快速代码库搜索专家，按模式查找文件、搜索关键词、回答代码库问题 | Edit, Write, NotebookEdit, Agent |
| `Plan` | 软件架构规划专家，设计实现方案，识别关键文件，考虑架构权衡 | Edit, Write, NotebookEdit |
| `general-purpose` | 通用型 Agent，处理复杂多步骤任务 | 无 |

### 自定义 Agent

在 `.nextcode/agents/` 目录创建 Markdown 文件：

```markdown
---
agent_type: my-agent
name: My Agent
description: 当需要执行特定任务时使用此 Agent
tools: ["Read", "Glob", "Grep"]
omit_claude_md: true
---
```

### Agent 调用

在对话中可以直接调用 Agent：

```
帮我探索这个代码库的结构
```

Agent 会自动被选择并执行，结果会流式返回给父 Agent。

## Skill 系统

### 创建 Skill

在 `.nextcode/skills/<name>/SKILL.md` 或 `.nextcode/skills/<name>.md` 创建 Skill 文件：

```markdown
---
description: 重写代码以提高性能
allowed_tools: ["Read", "Edit", "Glob", "Grep"]
model: deepseek-reasoner
context: inline
agent: Plan
when_to_use: 当代码存在性能问题需要优化时
argument_hint: <需要优化的代码片段或文件路径>
user_invocable: true
paths: ["src/**/*.py"]
---

你是性能优化专家。分析用户提供的代码，识别性能瓶颈，并提供优化建议。
```

### Skill 字段说明

| 字段 | 说明 |
|------|------|
| `description` | Skill 描述（显示给用户） |
| `allowed_tools` | 允许使用的工具列表 |
| `model` | 使用的模型（可选） |
| `context` | 上下文模式：`inline`（继承对话）或 `fork`（独立分支） |
| `agent` | 使用的 Agent 类型 |
| `when_to_use` | 何时使用此 Skill 的提示 |
| `argument_hint` | 参数提示 |
| `user_invocable` | 是否允许用户直接调用 |
| `paths` | 前置条件路径模式（非空时为条件激活） |
| `hooks` | 钩子配置 |

### 条件激活

带有 `paths` 的 Skill 会保持待激活状态，直到用户操作匹配的文件：

```markdown
---
name: frontend
paths: ["frontend/**/*", "src/**/*.tsx", "src/**/*.jsx"]
---
```

当用户读取或编辑 `frontend/` 目录下的文件时，该 Skill 会被激活。

### Skill 目录发现

支持渐进式 Skill 目录发现，从文件路径向上查找 `.nextcode/skills/`，适用于 monorepo 结构。

## 配置

### 配置优先级

设置按以下顺序加载（后者覆盖前者）：
1. 默认值
2. 全局配置 `~/.nextcode/.settings.json`
3. 项目配置 `.nextcode/.settings.json`
4. 环境变量

### 完整配置示例

```json
{
  "api_key": "sk-your-api-key",
  "model": "deepseek-reasoner",
  "context": {
    "max_tokens": 65536,
    "compact_threshold": 0.8,
    "auto_compact_buffer": 13000,
    "blocking_buffer": 3000,
    "circuit_breaker_max_failures": 3,
    "microcompact_keep_recent": 3
  },
  "permissions": {
    "allow": ["Read:*", "Glob:*", "Grep:*"],
    "deny": [],
    "ask": ["Bash:*", "Edit:*", "Write:*", "Agent:*"]
  }
}
```

### 环境变量

| 变量 | 说明 |
|------|------|
| `NEXTCODE_API_KEY` | DeepSeek 和 OpenAI 兼容模型的 API Key |
| `ANTHROPIC_API_KEY` | Claude 模型的 API Key |
| `ANTHROPIC_BASE_URL` | Anthropic API 地址（可选） |
| `NEXTCODE_BASE_URL` | OpenAI 兼容 API 地址（可选） |
| `NEXTCODE_MODEL` | 覆盖默认模型 |

## 支持的模型

| 模型 | 上下文窗口 | 最大输出 |
|------|-----------|---------|
| Claude Opus 4 | 200K tokens | 32K |
| Claude Sonnet 4 | 200K tokens | 16K |
| Claude 3.5 Sonnet | 200K tokens | 8K |
| Claude 3.5 Haiku | 200K tokens | 8K |
| DeepSeek | 128K tokens | 8K |
| MiniMax | 200K tokens | 8K |

## 技术栈

| 层级 | 技术选型 |
|------|---------|
| **后端** | Python 3.12+ |
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
│   │   ├── ipc/                  # IPC 传输层
│   │   ├── hooks/                # React hooks
│   │   └── utils/                # Markdown 渲染等工具
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
    │   ├── agent_tool.py         # Agent 调用工具
    │   └── skill.py              # Skill 调用工具
    ├── commands/                 # 命令系统（注册/调度/补全）
    ├── compact/                  # 上下文压缩（MICRO/FULL/BLOCKING）
    ├── permissions/              # 权限管理
    ├── prompts/                  # System Prompt 组装
    ├── state/                    # 状态管理（Store + ToolUseContext）
    ├── ipc/                      # IPC 桥接（Python ↔ Ink）
    ├── context/                  # 系统/用户上下文
    ├── agents/                   # Agent 系统
    │   ├── definition.py         # Agent 定义数据蓝图
    │   ├── runner.py            # runAgent() 生命周期引擎
    │   ├── builtins.py           # 内置 Agent 注册
    │   ├── loader.py             # 自定义 Agent 加载
    │   ├── markdown_parser.py    # Markdown 解析
    │   └── prompts/              # Agent 专用提示词
    └── skills/                   # Skill 系统
        ├── frontmatter.py        # YAML frontmatter 解析
        ├── loader.py             # Skill 目录加载
        ├── create_command.py     # PromptCommand 创建
        └── conditional.py        # 条件激活管理
```

## 架构

### 双进程 IPC 架构

```
┌─────────────────────────────────────────────────────────────┐
│                        Python 后端                          │
├─────────────────────────────────────────────────────────────┤
│  cli.py → init.py → setup.py → query.py                    │
│                                                              │
│  ├── config/       配置管理                                  │
│  ├── api/          多后端 API 客户端 (Anthropic/OpenAI)     │
│  ├── tools/        文件操作、shell 执行、Agent/Skill 调用   │
│  ├── commands/     斜杠命令 + PromptCommand                 │
│  ├── compact/      上下文压缩 (MICRO/FULL/BLOCKING)        │
│  ├── permissions/  权限管理                                  │
│  ├── state/        全局状态存储 + ToolUseContext            │
│  ├── agents/       Agent 定义、运行器、加载器               │
│  ├── skills/      Skill 加载、frontmatter、条件激活        │
│  └── ipc/          Unix Socket IPC 桥接                      │
└─────────────────────────────────────────────────────────────┘
                            │ Unix Socket
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                      Node.js 前端                            │
├─────────────────────────────────────────────────────────────┤
│  React + Ink 终端 UI                                         │
│  ├── WelcomeScreen    ASCII art + 模型信息                   │
│  ├── MessageList      流式文本、工具、Agent 事件             │
│  ├── InputBar         命令输入 + 补全                        │
│  └── PermissionDialog 权限确认对话框                         │
└─────────────────────────────────────────────────────────────┘
```

### Agent 生命周期

```
run_agent()
    │
    ├── Phase 1: 初始化
    │   ├── 解析模型（Agent 定义 > 父级 > 默认）
    │   └── 构建初始消息（过滤不完整的 tool_call）
    │
    ├── Phase 2: 权限与工具
    │   ├── 工具过滤（disallowed_tools / allowed_tools）
    │   └── 构建 Agent System Prompt
    │
    ├── Phase 4: Context 隔离
    │   └── create_subagent_context()
    │
    ├── Phase 5: 对话循环
    │   ├── QueryRunner 循环
    │   ├── 流式 yield QueryEvent
    │   └── agent_start / agent_result 事件
    │
    └── Phase 6: 清理
        └── 释放文件缓存等资源
```

### Skill 加载流程

```
load_skills_from_dir()
    │
    ├── 扫描 skills/ 目录
    │   ├── 新格式: <name>/SKILL.md
    │   └── 旧格式: <name>.md
    │
    ├── 懒惰加载
    │   ├── 注册时只读 frontmatter
    │   └── 调用时读取完整 Markdown
    │
    └── 条件激活
        ├── 有 paths → 待激活（pending）
        └── 无 paths → 直接激活（activated）
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
       ├── query.text_delta ──────────────>│
       ├── query.thinking_delta ───────────>│
       ├── query.tool_start ───────────────>│
       ├── query.tool_use ─────────────────>│
       ├── query.tool_result ─────────────>│
       ├── query.complete ─────────────────>│
       │                                    │
       ├── agent.agent_start ──────────────>│
       ├── agent.query.text_delta ─────────>│
       ├── agent.query.tool_start ─────────>│
       ├── agent.query.complete ───────────>│
       ├── agent.agent_result ─────────────>│
       │                                    │
       ├── permission.request ─────────────>│
       │<── permission.response ───────────│
       │                                    │
       ├── compact.started ────────────────>│
       ├── compact.complete ────────────────>│
       └── context.info ───────────────────>│
```

### 上下文压缩

当对话历史增长时，NextCode 会自动压缩上下文：

| 级别 | 触发时机 | 行为 |
|------|---------|------|
| MICRO | 接近阈值 | 清除旧工具结果，保留最近 3 条 |
| FULL | 达到阈值 | 模型生成摘要 + 保留近期消息 |
| BLOCKING | 超出限制 | 拒绝新查询，需手动 `/compact` |

## 开发

### 运行测试

```bash
# 单元测试
pytest tests/unit/ -v

# 集成测试
pytest tests/integration/ -v

# 所有测试
pytest tests/ -v
```

## 故障排除

### API Key 未配置

```
Error: No API key found. Set NEXTCODE_API_KEY or run 'nextcode auth'.
```

解决：运行 `nextcode auth` 或设置 `NEXTCODE_API_KEY` / `ANTHROPIC_API_KEY` 环境变量。

### 模块未找到

```
ModuleNotFoundError: No module named 'next_code'
```

解决：在项目目录运行 `pip install -e .`

### Ink 前端不可用

```
Error: Ink frontend unavailable: Ink frontend bundle not found
```

解决：在 `frontend/` 目录运行 `npm install && npm run build`。

### 上下文窗口满

```
Context window full. Use /compact to manually compress the conversation.
```

解决：输入 `/compact` 手动压缩，或 `/clear` 清空对话。

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
| Agent 系统（Explore/Plan/General-Purpose） | ✅ |
| 自定义 Agent 支持 | ✅ |
| Agent 工具过滤与上下文隔离 | ✅ |
| Skill 系统（Markdown → PromptCommand） | ✅ |
| Skill 懒惰加载 | ✅ |
| Skill 条件激活 | ✅ |
| 单元测试 | ✅ |

## 贡献

欢迎贡献代码！请提交 Pull Request。

1. Fork 仓库
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add some amazing feature'`)
4. 推送分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

## 文档

- [需求设计文档](nextcode/需求设计文档.md)
- [项目架构文档](nextcode/项目架构文档.md)
- [使用文档](nextcode/使用文档.md)
- [CHANGELOG](nextcode/CHANGELOG.md)
- [实现计划](nextcode/docs/) — 上下文管理、System Prompt、对话循环、命令系统等

## 许可证

[MIT](LICENSE)

## 致谢

- 灵感来自 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) 和 [Claude Desktop](https://claude.ai/download)
- 基于 [Anthropic API](https://docs.anthropic.com/)、[OpenAI API](https://platform.openai.com/) 和 [DeepSeek API](https://platform.deepseek.com/) 构建
- 终端 UI 由 [Ink](https://github.com/vadimdemedes/ink) 和 [React](https://react.dev/) 驱动