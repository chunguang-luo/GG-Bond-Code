# Prompt Cache 实现计划

> 基于原文档：`claude-code-docs/07-Prompt-Cache.md`

## 文档核心要点总结

### 核心概念

| 概念 | 定义 |
|------|------|
| Prompt Cache | Anthropic API 的服务端缓存机制，对重复前缀的 input token 降低 90% 成本 |
| `cache_control` | API 请求体中的标记字段，`{ type: 'ephemeral' }`，告诉服务端"缓存到这里" |
| 缓存前缀 | 从请求开头到最后一个 `cache_control` 标记之间的所有内容 |
| 缓存作用域 | `global`（所有用户共享）、`org`（组织级共享）、`null`（不缓存） |
| 缓存 TTL | 默认 5 分钟，符合条件的用户可获得 1 小时 |
| 字节一致性 | 缓存匹配要求前缀字节完全一致，哪怕一个空格差异都会导致缓存失效 |

### 关键设计模式

1. **分层缓存标记 + 静态/动态分界**：用 `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` 将 System Prompt 拆为静态部分（可缓存）和动态部分（不缓存）
2. **Latch 锁存模式**：TTL、beta headers、feature flags 等在 session 开始时评估一次并锁存，防止中途翻转破坏缓存
3. **Fork 子进程 byte-exact 参数传递**：复用父线程已渲染的字节而非重新计算，保证前缀一致性
4. **Tool Schema Cache**：工具定义在 session 内只计算一次并缓存，防止描述抖动
5. **Cached Microcompact**：缓存有效时通过 `cache_edits` API 清理内容，而非直接修改消息（避免前缀字节变化）
6. **Cache Break Detection**：12 维度监控缓存失效，pre-call 快照 + post-call token 统计对比

---

## 项目当前实现分析

### 相关代码文件

| 文件 | 相关性 | 当前实现 |
|------|--------|---------|
| `api/client.py` | 高 | 有 `_split_system_prompt()` 拆分逻辑，但 TODO 未实现缓存标记注入 |
| `prompts/system.py` | 高 | 已定义 `SYSTEM_PROMPT_DYNAMIC_BOUNDARY`，`build_system_prompt()` 返回含边界标记的列表 |
| `query.py` | 高 | 构建系统 prompt 和消息历史，调用 `stream_message()`，无缓存相关逻辑 |
| `compact/micro.py` | 中 | 有 time-based microcompact，无 Cached Microcompact 路径 |
| `tools/base.py` | 中 | `to_api_format()` 每次重新序列化，无 toolSchemaCache |
| `context/system.py` | 中 | `@lru_cache` 缓存 Git 状态，但无 latch 模式保护 |
| `context/user.py` | 中 | `@lru_cache` 缓存用户上下文，但 prepend 方式破坏缓存前缀 |

### 完成度评估

| 功能点 | 文档要求 | 当前实现 | 状态 |
|--------|---------|---------|------|
| System Prompt 分块 | 静态/动态拆分 + 不同 cache_scope | 有 `_split_system_prompt()` 但未注入 `cache_control` | 🟡 部分实现 |
| `cache_control` 标记注入 | System Prompt 块 + 最后一条消息 | 完全未实现 | ❌ 未实现 |
| System Prompt 列表格式 | `list[TextBlockParam]` 带 `cache_control` | 当前合并为单个字符串 | ❌ 未实现 |
| 缓存作用域控制 | global / org / null | 完全未实现 | ❌ 未实现 |
| 缓存 TTL 管理 | 5min vs 1h + latch 锁存 | 完全未实现 | ❌ 未实现 |
| Tool Schema Cache | session 级缓存，防止描述抖动 | 每次请求重新序列化 | ❌ 未实现 |
| Cached Microcompact | cache_edits / cache_reference API | 只有 time-based 路径 | ❌ 未实现 |
| Cache Break Detection | 12 维度监控 | 完全未实现 | ❌ 未实现 |
| Fork Agent 缓存共享 | CacheSafeParams + byte-exact | 无 Fork Agent 机制 | ❌ 未实现 |
| 缓存命中统计 | 读取 API 响应中 cache_read_tokens | 未解析缓存相关 usage 字段 | ❌ 未实现 |

---

## 差异对比分析

| 维度 | 文档要求 | 当前实现 | 差异等级 | 备注 |
|------|---------|---------|----------|------|
| System Prompt 格式 | `list[dict]` 带 `cache_control` | 单个字符串 `str` | 🔴 高 | 核心变更，需重构 API 调用签名 |
| `cache_control` 注入 | System Prompt 块 + 消息末尾 | 无 | 🔴 高 | 核心功能缺失 |
| 缓存作用域 | global / org / null 三级 | 无 | 🟡 中 | 仅 Anthropic 后端需要 |
| Tool Schema 稳定性 | session 级缓存 | 每次重建 | 🟡 中 | 影响缓存命中率 |
| Cached Microcompact | cache_edits API | 仅 time-based | 🟡 中 | Anthropic 专属 API，优先级较低 |
| Fork Agent 缓存 | CacheSafeParams | 无 Fork Agent | 🟢 低 | 当前无此功能，可后置 |
| Cache Break Detection | 12 维度监控 | 无 | 🟢 低 | 调试辅助，可后置 |

---

## 实现方案

### P0：核心缓存标记注入（必须实现）

> 让 API 请求正确携带 `cache_control` 标记，实现基本的 Prompt Cache 命中。

#### 涉及文件

| 操作 | 文件 |
|------|------|
| 修改 | `api/client.py` |
| 修改 | `prompts/system.py` |
| 修改 | `query.py` |
| 新建 | `api/cache.py` |

#### 任务清单

**任务 1：新建 `api/cache.py` — 缓存控制模块**

```python
"""Prompt Cache control — cache_control marker injection for Anthropic API."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class CacheScope(str, Enum):
    """Cache sharing scope for cache_control markers."""

    GLOBAL = "global"  # All users share cache
    ORG = "org"        # Organization-level sharing (default in API)
    NONE = "none"      # No cache marker


@dataclass
class CacheControlConfig:
    """Configuration for prompt cache behavior."""

    enabled: bool = True
    scope: CacheScope = CacheScope.ORG
    ttl_1h: bool = False  # Whether to request 1-hour TTL


def get_cache_control(
    scope: CacheScope = CacheScope.ORG,
    ttl_1h: bool = False,
) -> dict[str, Any] | None:
    """Build cache_control marker for a content block.

    Args:
        scope: Cache sharing scope.
        ttl_1h: Whether to request 1-hour TTL (requires eligibility).

    Returns:
        cache_control dict, or None if scope is NONE.
    """
    if scope == CacheScope.NONE:
        return None

    result: dict[str, Any] = {"type": "ephemeral"}
    if ttl_1h:
        result["ttl"] = "1h"
    if scope == CacheScope.GLOBAL:
        result["scope"] = "global"
    # ORG scope is default — no scope field in API request
    return result


def build_system_prompt_blocks(
    static_sections: list[str],
    dynamic_sections: list[str],
    config: CacheControlConfig,
) -> list[dict[str, Any]]:
    """Build Anthropic system prompt blocks with cache_control markers.

    Mirrors Claude Code's buildSystemPromptBlocks():
    - Static sections: cache with configured scope (global or org)
    - Dynamic sections: no cache marker (scope=null)

    Args:
        static_sections: Static prompt sections (shared across users).
        dynamic_sections: Dynamic prompt sections (user-specific).
        config: Cache control configuration.

    Returns:
        List of TextBlockParam dicts for Anthropic API system parameter.
    """
    if not config.enabled:
        # No caching — merge everything into one block
        combined = "\n\n".join(static_sections + dynamic_sections)
        return [{"type": "text", "text": combined}]

    blocks: list[dict[str, Any]] = []

    # Static block(s) with cache_control
    if static_sections:
        static_text = "\n\n".join(static_sections)
        cache_marker = get_cache_control(scope=config.scope, ttl_1h=config.ttl_1h)
        block: dict[str, Any] = {"type": "text", "text": static_text}
        if cache_marker:
            block["cache_control"] = cache_marker
        blocks.append(block)

    # Dynamic block(s) without cache_control
    if dynamic_sections:
        dynamic_text = "\n\n".join(dynamic_sections)
        blocks.append({"type": "text", "text": dynamic_text})

    return blocks


def add_cache_breakpoint_to_messages(
    messages: list[dict[str, Any]],
    config: CacheControlConfig,
) -> list[dict[str, Any]]:
    """Add cache_control marker to the last message for message-level caching.

    Only one cache_control marker per request — on the last message.
    This maximizes the cached prefix length.

    Args:
        messages: Message list in Anthropic API format.
        config: Cache control configuration.

    Returns:
        Messages with cache_control injected on the last message.
    """
    if not config.enabled or not messages:
        return messages

    cache_marker = get_cache_control(scope=config.scope, ttl_1h=config.ttl_1h)
    if not cache_marker:
        return messages

    result = [msg.copy() for msg in messages]

    # Add cache_control to the last message's last content block
    last_msg = result[-1]
    if isinstance(last_msg.get("content"), list):
        # Anthropic format: content is list of blocks
        new_blocks = []
        for i, block in enumerate(last_msg["content"]):
            if i == len(last_msg["content"]) - 1:
                # Last block gets the marker
                new_block = {**block, "cache_control": cache_marker}
                new_blocks.append(new_block)
            else:
                new_blocks.append(block)
        result[-1] = {**last_msg, "content": new_blocks}
    elif isinstance(last_msg.get("content"), str):
        # Convert string content to block list and add marker
        result[-1] = {
            **last_msg,
            "content": [
                {"type": "text", "text": last_msg["content"], "cache_control": cache_marker}
            ],
        }

    return result
```

**任务 2：修改 `api/client.py` — 使用缓存模块**

核心变更：`_stream_anthropic_inner()` 接收 system prompt 块列表而非字符串，注入 `cache_control` 标记。

```python
# === 修改 _stream_anthropic_inner 签名 ===
# 之前:
async def _stream_anthropic_inner(
    messages, tools, system: str, model, max_tokens
) -> AsyncIterator[dict[str, Any]]:

# 之后:
async def _stream_anthropic_inner(
    messages, tools, system_blocks: list[dict[str, Any]],
    model, max_tokens
) -> AsyncIterator[dict[str, Any]]:
    client = _get_anthropic_client()
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_blocks,  # 传入块列表而非字符串
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    # ...rest unchanged
```

**任务 3：修改 `stream_message()` — 组装缓存块**

```python
# === 修改 stream_message() 中 Anthropic 分支 ===
# 之前 (lines 427-433):
static_blocks, dynamic_blocks = _split_system_prompt(system)
combined_system = "\n\n".join(static_blocks + dynamic_blocks)
# TODO: ...

# 之后:
from .cache import CacheControlConfig, build_system_prompt_blocks, add_cache_breakpoint_to_messages

static_sections, dynamic_sections = _split_system_prompt(system)

if family == "anthropic":
    config = CacheControlConfig(enabled=True, scope=CacheScope.ORG)
    system_blocks = build_system_prompt_blocks(static_sections, dynamic_sections, config)
    messages = add_cache_breakpoint_to_messages(messages, config)
    async for evt in _stream_anthropic(messages, tools, system_blocks, model, max_tokens):
        yield evt
else:
    # OpenAI-compatible: no prompt cache support, merge as before
    combined_system = "\n\n".join(static_sections + dynamic_sections)
    combined_system = _sanitize_surrogates(combined_system)
    async for evt in _stream_openai(messages, tools, combined_system, model, max_tokens):
        yield evt
```

**任务 4：解析缓存统计信息**

在 `_stream_anthropic_inner()` 中解析 API 响应的缓存 token 统计：

```python
# 在获取 final_message 后添加:
message = await stream.get_final_message()

# 提取缓存统计
if hasattr(message, 'usage') and message.usage:
    cache_stats = {
        "cache_creation_input_tokens": getattr(message.usage, 'cache_creation_input_tokens', 0),
        "cache_read_input_tokens": getattr(message.usage, 'cache_read_input_tokens', 0),
        "input_tokens": message.usage.input_tokens,
    }
    # 通过事件传递缓存统计
    yield {"type": "cache_stats", "stats": cache_stats}
```

---

### P1：Tool Schema Cache + Latch 模式（重要优化）

> 防止工具定义抖动和配置翻转导致缓存意外失效。

#### 涉及文件

| 操作 | 文件 |
|------|------|
| 修改 | `tools/base.py` |
| 新建 | `api/cache.py`（扩展） |
| 修改 | `query.py` |

#### 任务清单

**任务 5：Tool Schema Session 缓存**

在 `ToolRegistry` 中添加 session 级别的 schema 缓存，防止 `to_api_format()` 每次重新序列化：

```python
# tools/base.py — ToolRegistry 扩展
class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._schema_cache: dict[str, dict[str, Any]] = {}  # session-level cache

    def to_api_format(self, family: str = "openai") -> list[dict[str, Any]]:
        result = []
        for name, tool in self._tools.items():
            if name not in self._schema_cache:
                self._schema_cache[name] = tool.to_api_format(family)
            result.append(self._schema_cache[name])
        return result

    def invalidate_schema_cache(self, tool_name: str | None = None) -> None:
        """Invalidate schema cache for a tool, or all tools if None."""
        if tool_name:
            self._schema_cache.pop(tool_name, None)
        else:
            self._schema_cache.clear()
```

**任务 6：Latch 模式保护缓存配置**

```python
# api/cache.py — 扩展 CacheControlConfig
@dataclass
class CacheControlConfig:
    """Cache control configuration with latch protection."""

    enabled: bool = True
    scope: CacheScope = CacheScope.ORG
    ttl_1h: bool = False

    # Latch fields — set once per session, never flip
    _latched_ttl_1h: bool | None = None
    _latched_scope: CacheScope | None = None

    @property
    def effective_ttl_1h(self) -> bool:
        """Get TTL with latch protection — once True, never goes back to False."""
        if self._latched_ttl_1h is None:
            self._latched_ttl_1h = self.ttl_1h
        return self._latched_ttl_1h

    @property
    def effective_scope(self) -> CacheScope:
        """Get scope with latch protection."""
        if self._latched_scope is None:
            self._latched_scope = self.scope
        return self._latched_scope

    def reset_latch(self) -> None:
        """Reset latches — only on /clear or /compact."""
        self._latched_ttl_1h = None
        self._latched_scope = None
```

**任务 7：在 query.py 中集成缓存配置**

```python
# query.py — QueryRunner 初始化
class QueryRunner:
    def __init__(self, ...):
        # ...existing init...
        self._cache_config = CacheControlConfig(enabled=True, scope=CacheScope.ORG)

    async def run(self, user_message: str) -> AsyncIterator[QueryEvent]:
        # ...existing code...
        # Pass cache config to stream_message
        async for evt in stream_message(
            messages=messages,
            tools=tools,
            system=full_system_prompt,
            model=self.model,
            cache_config=self._cache_config,
        ):
            # ...existing event handling...
            if evt.get("type") == "cache_stats":
                # Log cache statistics for debugging
                logger.debug(f"Cache stats: {evt['stats']}")
                continue
```

---

### P2：Cache Break Detection + Cached Microcompact（可选优化）

> 缓存失效检测和缓存安全的上下文清理。

#### 涉及文件

| 操作 | 文件 |
|------|------|
| 新建 | `api/cache_break_detection.py` |
| 修改 | `compact/micro.py` |
| 修改 | `query.py` |

#### 任务清单

**任务 8：Cache Break Detection 基础框架**

```python
# api/cache_break_detection.py
"""Cache break detection — monitor prompt cache hit rate and diagnose misses."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PromptStateSnapshot:
    """Snapshot of prompt state for cache break detection."""

    system_prompt_hash: str = ""
    tools_hash: str = ""
    model: str = ""
    cache_scope: str = ""
    cache_ttl: str = ""


class CacheBreakDetector:
    """Detect and log prompt cache breaks.

    Monitors changes in prompt state across turns and correlates
    with API cache_read_tokens to diagnose cache misses.
    """

    def __init__(self) -> None:
        self._last_snapshot: PromptStateSnapshot | None = None
        self._last_cache_read_tokens: int = 0

    def record_prompt_state(self, system_prompt: str, tools: list[dict], model: str,
                           cache_scope: str, cache_ttl: str) -> None:
        """Record pre-call prompt state snapshot."""
        snapshot = PromptStateSnapshot(
            system_prompt_hash=hashlib.md5(system_prompt.encode()).hexdigest()[:12],
            tools_hash=hashlib.md5(str(tools).encode()).hexdigest()[:12],
            model=model,
            cache_scope=cache_scope,
            cache_ttl=cache_ttl,
        )

        if self._last_snapshot:
            changes = self._detect_changes(self._last_snapshot, snapshot)
            if changes:
                logger.info(f"Prompt state changes: {changes}")

        self._last_snapshot = snapshot

    def check_cache_break(self, cache_read_tokens: int) -> dict[str, Any] | None:
        """Check if a cache break occurred based on API response."""
        if self._last_cache_read_tokens > 0 and cache_read_tokens > 0:
            drop_ratio = 1 - (cache_read_tokens / self._last_cache_read_tokens)
            if drop_ratio > 0.05:  # 5% threshold
                return {
                    "cache_break_detected": True,
                    "previous_read": self._last_cache_read_tokens,
                    "current_read": cache_read_tokens,
                    "drop_ratio": round(drop_ratio, 3),
                }
        self._last_cache_read_tokens = cache_read_tokens
        return None

    def _detect_changes(self, prev: PromptStateSnapshot, curr: PromptStateSnapshot) -> list[str]:
        """Detect what changed between two snapshots."""
        changes = []
        if prev.system_prompt_hash != curr.system_prompt_hash:
            changes.append("system_prompt_changed")
        if prev.tools_hash != curr.tools_hash:
            changes.append("tool_schemas_changed")
        if prev.model != curr.model:
            changes.append("model_changed")
        if prev.cache_scope != curr.cache_scope:
            changes.append("cache_scope_changed")
        if prev.cache_ttl != curr.cache_ttl:
            changes.append("cache_ttl_changed")
        return changes
```

**任务 9：Cached Microcompact 路径（Anthropic 专属）**

在 `compact/micro.py` 中添加缓存感知路径：当缓存仍有效时，不直接修改消息内容，而是记录需要清理的 tool_use_id 列表，在 API 调用时通过 `cache_edits` 注入。

> 注意：`cache_edits` 和 `cache_reference` 是 Anthropic 的专有 API 扩展，目前仍在 beta 阶段。建议先实现 P0 和 P1，待 API 稳定后再实现此路径。

---

## 测试与验证计划

### 单元测试

| 测试文件 | 测试用例 |
|---------|---------|
| `tests/unit/test_cache.py`（新建） | `test_get_cache_control_returns_none_for_no_scope` |
| | `test_get_cache_control_global_scope` |
| | `test_get_cache_control_org_scope_no_scope_field` |
| | `test_get_cache_control_1h_ttl` |
| | `test_build_system_prompt_blocks_no_cache` |
| | `test_build_system_prompt_blocks_static_cached` |
| | `test_build_system_prompt_blocks_dynamic_not_cached` |
| | `test_add_cache_breakpoint_on_last_message` |
| | `test_add_cache_breakpoint_no_duplicate_markers` |
| `tests/unit/test_cache_break_detection.py`（新建） | `test_detect_system_prompt_change` |
| | `test_detect_model_change` |
| | `test_detect_cache_break_by_token_drop` |
| | `test_no_false_positive_on_small_change` |

### 集成测试

| 场景 | 验证步骤 |
|------|---------|
| 基本缓存命中 | 1. 发送第 1 轮请求 → 检查 `cache_creation_input_tokens > 0` |
| | 2. 发送第 2 轮请求（相同前缀）→ 检查 `cache_read_input_tokens > 0` |
| 缓存失效 | 1. 修改 system prompt → 发送请求 → 检查缓存重建 |
| OpenAI 兼容性 | 1. 使用 DeepSeek 模型 → 确认不走缓存路径 → 请求正常 |
| Microcompact | 1. 触发 time-based microcompact → 确认消息被清理 |

### 验证清单

- [ ] Anthropic API 请求中 system 参数为 `list[dict]` 格式
- [ ] 静态 system prompt 块带有 `cache_control` 标记
- [ ] 最后一条用户消息带有 `cache_control` 标记
- [ ] API 响应中 `cache_read_input_tokens > 0`（第 2 轮起）
- [ ] OpenAI 兼容后端不受影响（无 cache_control 注入）
- [ ] Tool schema 在 session 内缓存，不重复序列化
- [ ] `/clear` 和 `/compact` 正确重置缓存状态
- [ ] 缓存配置 latch 在 session 内不会翻转

---

## 风险评估

### 架构风险

| 风险 | 影响 | 缓解策略 |
|------|------|---------|
| System Prompt 从 `str` 变为 `list[dict]` 破坏 API 签名 | 高 | 仅在 Anthropic 路径使用列表格式，OpenAI 路径保持字符串 |
| `cache_control` 标记位置错误导致缓存命中率低 | 中 | 单元测试覆盖所有标记位置；Cache Break Detection 辅助调试 |
| Anthropic SDK 版本对 `cache_control` 支持差异 | 中 | 检查 `anthropic>=0.40.0` 是否支持所需字段 |

### 性能风险

| 风险 | 影响 | 缓解策略 |
|------|------|---------|
| 首次请求缓存写入增加延迟 | 低 | 缓存写入成本远低于后续命中收益 |
| Tool Schema 缓存占用内存 | 低 | 每个 schema 几 KB，6 个工具总计 < 50KB |

### 向后兼容风险

| 风险 | 影响 | 缓解策略 |
|------|------|---------|
| 现有 OpenAI 后端用户不受影响 | 低 | 缓存逻辑仅 Anthropic 路径激活 |
| 配置文件新增缓存相关字段 | 低 | 全部有默认值，无需用户手动配置 |

---

## 可迁移的设计模式

### 模式 1：分层缓存标记

- **类别**：行为模式
- **描述**：将请求内容分为"所有用户共享的静态部分"和"用户特定的动态部分"，用不同的缓存作用域标记
- **原始来源**：Claude Code `services/api/claude.ts:3213`
- **适用场景**：任何调用 Anthropic API 且有长 System Prompt 的应用
- **Python 实现示例**：

```python
# 静态部分 → cache_control with scope=global
# 动态部分 → 无 cache_control
system_blocks = [
    {"type": "text", "text": static_content, "cache_control": {"type": "ephemeral", "scope": "global"}},
    {"type": "text", "text": dynamic_content},  # 无 cache_control
]
```

### 模式 2：Latch 锁存防翻转

- **类别**：行为模式
- **描述**：影响缓存键的配置在 session 开始时评估一次并锁存，整个 session 不再重新评估
- **原始来源**：Claude Code `services/api/claude.ts:393`
- **适用场景**：使用 feature flags / A/B testing 且关心缓存一致性的系统
- **Python 实现示例**：

```python
class LatchedValue:
    """A value that, once set, cannot flip during the session."""
    def __init__(self):
        self._value = None
        self._latched = False

    def get(self, compute_fn):
        if not self._latched:
            self._value = compute_fn()
            self._latched = True
        return self._value

    def reset(self):
        self._value = None
        self._latched = False
```

### 模式 3：Byte-exact 参数传递

- **类别**：实现模式
- **描述**：在 fork 子进程中直接传递父线程已序列化的字节，而非重新计算
- **原始来源**：Claude Code `tools/AgentTool/forkSubagent.ts:44`
- **适用场景**：并行执行多个 API 请求且希望共享缓存前缀的场景
- **Python 实现示例**：

```python
# 错误：重新生成 system prompt（可能字节不同）
system_for_fork = build_system_prompt()  # GrowthBook 状态可能已变化

# 正确：直接传递父线程已渲染的字节
system_for_fork = parent_rendered_system_prompt  # byte-exact
```

---

## 实施优先级总结

```
P0 (必须实现) ─────────────────────────────────────
  任务1: api/cache.py 缓存控制模块
  任务2: client.py Anthropic 路径注入 cache_control
  任务3: stream_message() 组装缓存块
  任务4: 解析缓存统计信息

P1 (重要优化) ─────────────────────────────────────
  任务5: Tool Schema Session 缓存
  任务6: Latch 模式保护缓存配置
  任务7: query.py 集成缓存配置

P2 (可选优化) ─────────────────────────────────────
  任务8: Cache Break Detection 基础框架
  任务9: Cached Microcompact 路径（待 API 稳定）
```