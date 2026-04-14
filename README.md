# GG-Bond-Code

用 Python 重新实现 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI 的核心主流程，保留架构精髓，去掉企业级复杂度。

## 特性

- **交互式 REPL** — 流式对话，支持 Markdown 渲染
- **多模型支持** — DeepSeek / Claude，自动选择 API 后端
- **核心工具集** — Bash、FileRead、FileEdit、FileWrite、Glob、Grep
- **权限系统** — 工具执行 allow/deny 控制，交互式确认，通配符授权持久化
- **Thinking 展示** — 实时流式显示思考过程，`/thinking` 命令切换
- **API 重试 + 超时** — 429/5xx 自动指数退避重试，请求超时保护
- **工具安全执行** — 超时保护 + 统一异常捕获
- **分层配置** — 全局 + 项目级配置，环境变量覆盖
- **斜杠命令** — `/help`、`/clear`、`/compact`、`/thinking`、`/model`、`/exit`

## 快速开始

### 安装

```bash
cd gg-bond-code
pip install -e .
```

### 配置 API Key

```bash
# DeepSeek 模型
export GGBOND_API_KEY=your-deepseek-api-key

# Claude 模型
export ANTHROPIC_API_KEY=your-anthropic-api-key

# 或交互式配置
ggbond auth
```

### 使用

```bash
# 交互模式
ggbond

# 非交互模式
echo "解释什么是 Python 装饰器" | ggbond --print

# 指定模型
ggbond --model deepseek-reasoner
ggbond --model claude-sonnet-4-20250514
```

## 技术栈

| 层级 | 选型 |
|------|------|
| 语言 | Python 3.12+ |
| CLI 框架 | Click |
| 终端 UI | Rich |
| Schema 验证 | Pydantic |
| API 客户端 | anthropic SDK / openai SDK |
| 异步 | asyncio |
| 配置格式 | JSON |

## 项目结构

```
gg-bond-code/
├── pyproject.toml
└── src/gg_bond_code/
    ├── cli.py              # 入口 + 快速路径
    ├── main.py             # 命令编排
    ├── init.py             # 配置/认证/预连接初始化
    ├── setup.py            # 会话级初始化
    ├── repl.py             # REPL 循环 + Thinking 展示 + 权限 UI
    ├── query.py            # 对话循环核心 + 权限回调
    ├── config/             # 配置 + 认证
    ├── api/                # API 客户端（双后端 + 重试 + 超时）
    ├── tools/              # 工具集（Bash/Read/Edit/Write/Glob/Grep + execute_safe）
    ├── prompts/            # System Prompt 组装
    ├── permissions/        # 权限管理（allow/deny/ask + 持久化）
    └── state/              # 状态管理
```

## 实现状态

| 功能 | 状态 |
|------|------|
| CLI 入口 + 快速路径 | ✅ |
| 分层启动链路 | ✅ |
| 配置系统 | ✅ |
| 多模型支持 | ✅ |
| 交互式 REPL | ✅ |
| 斜杠命令 | ✅ |
| 对话循环 + 工具执行 | ✅ |
| 流式输出 | ✅ |
| 权限系统 + 持久化 | ✅ |
| API 重试 + 超时 | ✅ |
| 工具安全执行 | ✅ |
| Thinking 展示 | ✅ |
| 单元测试 | ✅ |
| 多 Agent 协调 | 📋 |
| Prompt Cache / 上下文压缩 | 📋 |
| Memory 系统 | 📋 |

## 文档

- [需求设计文档](gg-bond-code/需求设计文档.md)
- [项目架构文档](gg-bond-code/项目架构文档.md)
- [对话循环实现文档](gg-bond-code/2-对话循环实现文档.md)
- [使用文档](gg-bond-code/使用文档.md)
- [CHANGELOG](gg-bond-code/CHANGELOG.md)

## License

[MIT](LICENSE)
