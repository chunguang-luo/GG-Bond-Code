# Changelog

## [0.2.0] - 2026-04-14

### api/client.py — 流式事件 + 重试 + 超时

- **Anthropic 实时 tool_use 事件**：处理 `content_block_start` → yield `tool_start`，`input_json_delta` → yield `tool_input_delta`，`content_block_stop` → yield `tool_end`，替代原来 `message_stop` 后批量获取的方式
- **指数退避重试**：对 429 (rate limit) 和 5xx 错误自动重试，最多 3 次，退避间隔 1s/2s/4s；4xx 错误直接抛出不重试
- **超时配置**：新增 `httpx.Timeout(connect=10s, read=120s, write=30s, pool=10s)`，OpenAI 和 Anthropic 客户端均传入

### query.py — 核心循环修复 + 权限回调

- **修复 Anthropic 多工具结果合并**：所有 tool_result 放在同一个 `role: "user"` 消息的 content 数组中，避免 400 错误
- **权限确认回调**：新增 `permission_callback` 参数，`_check_permission()` 方法在 ASK 决策时调用回调；无回调时（print 模式）默认 DENY
- **新流式事件处理**：支持 `tool_start`、`tool_input_delta`、`tool_end` 事件类型
- **Store 复用**：`__init__` 中获取 store 实例，`run()` 中复用 `self.store`
- **使用 execute_safe**：`_execute_tool()` 调用 `tool.execute_safe()` 替代 `tool.execute()`

### permissions/manager.py — 交互式确认 + 持久化

- **ask_user() 方法**：交互式询问用户权限，支持 y(允许)/a(本次会话全部允许)/n(拒绝)
- **通配符会话授权**：`grant_session(wildcard=True)` 存储 `tool_name:*`，允许该工具的所有操作
- **权限持久化**：通配符授权自动写入 `.ggbond/.settings.json`，下次启动自动生效；非通配符授权仅保存在内存中
- **会话授权匹配优化**：`check()` 遍历 `_session_allowed` 时支持 fnmatch 通配符匹配

### repl.py — 交互体验

- **权限确认 UI**：REPL 构造时传入 `permission_callback`，通过 `asyncio.to_thread` 包装同步 `ask_user()`
- **Thinking 实时展示**：流式阶段实时显示 thinking 内容（`< Thinking > ... < /Thinking >`），默认关闭，`/thinking` 命令切换
- **工具执行进度**：处理 `tool_start` 事件显示工具名，`tool_result` 显示执行耗时
- **/clear 重建 QueryRunner**：清除对话时同时重建带 permission_callback 的 runner

### tools/base.py — 安全执行

- **execute_safe() 方法**：`asyncio.wait_for` 超时保护 + 统一异常捕获
- **get_timeout() 方法**：默认 120s 超时，子类可覆写

### tests/unit/ — 单元测试

- `test_api_client.py`：模型路由、重试配置、超时配置、`_is_retryable` 判断
- `test_permissions.py`：权限检查、通配符授权、持久化、deny 优先级、ask_user 存在性
- `test_query_runner.py`：Store 复用、权限回调、事件类型、无回调默认 DENY
- `test_repl.py`：REPL 回调绑定、参数格式化
- `test_tool_base.py`：execute_safe 正常/超时/异常、默认超时值

## [0.1.0] - 2026-04-13

### 初始版本

- CLI 入口 + 快速路径 (`cli.py`)
- Click 命令编排 (`main.py`)
- 配置/认证/预连接初始化 (`init.py`)
- 会话级初始化 (`setup.py`)
- 多层配置系统 (`config/settings.py`)
- API Key 解析 (`config/auth.py`)
- 双后端流式 API 客户端 (`api/client.py`)
- System Prompt 组装 (`prompts/system.py`)
- Tool 基类 + 注册表 + 6 个核心工具 (`tools/`)
- 全局状态单例 (`state/store.py`)
- 权限管理基础框架 (`permissions/manager.py`)
- 交互式 REPL (`repl.py`)
- 对话循环核心 (`query.py`)
- 相对导入重构
