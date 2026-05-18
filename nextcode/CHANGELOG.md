# Changelog

## [0.3.0] - 2026-04-15

### state/store.py — Store 订阅 + onChange + 深拷贝 + 重置

- **subscribe(listener)**：新增订阅机制，返回 unsubscribe 函数，对齐 Claude Code 的 `store.subscribe` API
- **onChange 回调**：构造函数接受 `on_change` 参数，`set()` 变更时先触发 onChange 再通知订阅者，支持集中式副作用
- **相等性检查**：`set()` 中对旧值做 `==` 比较，值未变时跳过通知，避免不必要的工作
- **snapshot() 深拷贝**：从 `dict(self._data)` 浅拷贝改为 `copy.deepcopy(self._data)`，防止外部通过引用修改 Store 内部数据
- **reset() 方法**：清空数据并通知所有监听者（`__reset__` key），保留监听者列表，支持 session 切换和测试隔离
- **reset_store() 模块函数**：重建全局单例，可选传入新的 onChange 回调

### state/context.py — ToolUseContext 运行时上下文容器

- **ToolUseContext dataclass**：解耦 QueryRunner 与全局 Store 的直接依赖，通过 `get_state/set_state` 函数间接访问状态
  - `set_state`：可替换为 no-op（子 Agent 隔离场景）
  - `set_state_for_tasks`：始终穿透到根 Store（对齐 Claude Code 的 `setAppStateForTasks`）
  - `abort`：asyncio.Event，支持取消信号
  - `agent_id/agent_type`：Agent 身份标识
- **create_store_context()**：创建连接真实 Store 的主循环上下文，未提供 registry 时自动加载默认工具
- **create_subagent_context()**：创建隔离的子 Agent 上下文
  - `set_state` 默认 no-op（子 Agent 不应修改 UI 状态）
  - `set_state_for_tasks` 始终穿透到父级根 Store
  - `share_abort/share_set_state` 可选共享标志

### config/settings.py — Store ↔ Settings 统一

- **update_setting(key, value)**：新增公共 API，同时更新内存 settings + 持久化到项目配置文件
- **is_persistable_key(key)**：判断 key 是否需要从 Store 自动同步回 Settings
- **_persist_to_project()**：将单个 key-value 写入项目 `.nextcode/.settings.json`

### setup.py — 会话初始化增强

- **Store ↔ Settings 桥接**：`reset_store(on_change=_on_store_change)`，持久化 key（如 model）变更时自动同步到 Settings
- **UI 偏好 Store 化**：`ui.show_thinking`、`ui.show_tool_details` 初始化到 Store，替代 REPL 实例属性

### query.py — QueryEvent tool_use_id + ToolUseContext 集成

- **QueryEvent.tool_use_id**：新增字段，用于工具计时精确匹配（替代之前按 tool_name 近似匹配）
- **ToolUseContext 集成**：QueryRunner 接受可选 `context: ToolUseContext` 参数，不再直接访问全局 Store
- **补发 tool_start 事件**：在非流式路径（Anthropic tool_use blocks / OpenAI tool_calls）中补发 `tool_start` 事件，确保 REPL 计时覆盖所有场景

### repl.py — 工具计时精确匹配

- **tool_use_id 匹配**：使用 `tool_use_id` 精确关联 tool_start 和 tool_result，替代之前遍历查找的近似匹配
- **tool_use 兜底计时**：`tool_use` 事件到达时若 `tool_start_times` 中无对应 ID，自动记录开始时间

### permissions/manager.py — 封装修复

- 使用 `update_setting()` 替代直接调用 `_save_json`，修复封装违规
- `_allowed`/`_denied` 使用 `list()` 拷贝，避免共享引用变异

### tests/unit/ — 新增测试

- `test_store.py`：283 行，覆盖 get/set/subscribe/onChange/reset/snapshot 深拷贝/相等性检查
- `test_context.py`：247 行，覆盖 ToolUseContext 创建、create_store_context、create_subagent_context 隔离行为
- `test_query_runner.py`：新增 `test_query_event_tool_use_id` 测试

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
- **权限持久化**：通配符授权自动写入 `.nextcode/.settings.json`，下次启动自动生效；非通配符授权仅保存在内存中
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
