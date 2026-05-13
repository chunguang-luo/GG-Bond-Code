# Prompt Cache 机制对比：为什么 Anthropic 需要手动控制缓存

## 一、为什么需要 Prompt Cache？

### 1.1 核心问题：Input Token 的重复传输

调用大模型 API 时，最大成本来自 **input tokens**。在一个典型的多轮对话中：

```
第 1 轮请求：
  [System Prompt 20K] [Tools 3K] [用户输入 0.5K] = 23,500 input tokens

第 2 轮请求：
  [System Prompt 20K] [Tools 3K] [历史消息 2K] [用户输入 0.5K] = 25,500 input tokens

第 50 轮请求：
  [System Prompt 20K] [Tools 3K] [历史消息 98K] [用户输入 0.5K] = 121,500 input tokens
```

每轮请求中，System Prompt 和工具定义完全相同，历史消息也是逐轮递增的。**大量 token 被重复发送和处理**。

50 轮对话总计约 **365 万 input tokens**，其中绝大部分是重复内容。

### 1.2 LLM 推理的两个阶段

理解缓存为什么有效，需要先理解 LLM 推理的两个阶段：

```
阶段 1 — Prefill（预填充）
  输入所有 input tokens → 计算注意力矩阵（KV Cache）
  复杂度：O(n²)  ← 这是最贵的步骤

阶段 2 — Decode（解码）
  基于 KV Cache → 逐 token 生成输出
  复杂度：O(n) per token
```

**Prefill 是瓶颈**。2 万 token 的 System Prompt 每次都要重新计算 O(n²) 的注意力，即使内容完全相同。

### 1.3 Prompt Cache 的核心原理

```
第 N 轮请求：[已缓存的 KV Cache A B C D E] + [新增内容 F]

服务端发现 [A B C D E] 的 KV Cache 已经存在（前缀字节一致）
→ 直接复用这份 KV Cache
→ 只对新增的 F 做 prefill
→ 跳过了最贵的 O(n²) 计算
```

缓存命中的 token 只需读取 KV Cache，成本仅为全价的 **10%**。

### 1.4 实际节省计算

```
50 轮对话，无缓存：365 万 tokens 全价
50 轮对话，有缓存：约 40.6 万 tokens 等价成本

节省：约 89%（接近 90%）
```

---

## 二、各模型提供商的 Prompt Cache 机制对比

### 2.1 总览表

| 提供商 | 缓存类型 | 最小前缀 | 折扣 | TTL | 开发者控制度 |
|---|---|---|---|---|---|
| **Anthropic (Claude)** | 显式手动标记 | 1,024 tokens | **90%** | 5 min（命中即刷新），可申请 1h | 高 |
| **OpenAI (GPT-4o/o1)** | 全自动 | 1,024 tokens | 50% | ~5-10 min | 低 |
| **Google Gemini** | 自动 + 显式 API | 1,024 / 32,768 tokens | 最高 75% | 可配置 | 中高 |
| **DeepSeek** | 全自动 | ~1,024 tokens | 最高 90% | ~5 min | 低 |

### 2.2 Anthropic：显式缓存（最高控制度，最高折扣）

#### 工作方式

开发者在 API 请求体中手动放置 `cache_control` 标记：

```json
{
  "model": "claude-sonnet-4-6",
  "system": [
    {
      "type": "text",
      "text": "你是一个助手...",
      "cache_control": { "type": "ephemeral" }
    }
  ],
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "帮我解释缓存",
          "cache_control": { "type": "ephemeral" }
        }
      ]
    }
  ]
}
```

#### 缓存规则

1. API 服务端缓存**从请求开头到最后一个 `cache_control` 标记**之间的所有内容
2. 下次请求如果前缀字节**完全一致**，从缓存读取（10% 成本）
3. 字节一致性要求极其严苛——哪怕一个空格、一个 JSON 字段顺序差异，缓存就失效

#### 三级缓存作用域

| 作用域 | wire 表现 | 含义 |
|--------|----------|------|
| `global` | `{ type: 'ephemeral', scope: 'global' }` | 所有用户共享一份缓存 |
| `org` | `{ type: 'ephemeral' }`（省略 scope） | 同组织用户共享 |
| `null`（无标记） | 不添加 `cache_control` | 不缓存 |

#### API 响应中的缓存统计

```json
// 首次请求（缓存写入）
{
  "usage": {
    "input_tokens": 500,
    "cache_creation_input_tokens": 23000,
    "cache_read_input_tokens": 0
  }
}

// 后续请求（缓存命中）
{
  "usage": {
    "input_tokens": 500,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 23000  // 90% 折扣！
  }
}
```

### 2.3 OpenAI：全自动缓存（零配置，低折扣）

#### 工作方式

**无需任何代码改动**。服务端自动检测最长公共前缀并缓存：

```python
# 普通 API 调用，不需要任何缓存相关参数
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": "你是一个助手..."},  # 自动缓存
        {"role": "user", "content": "你好"},
    ]
)
```

#### 特点

- **零配置**：开发者不需要做任何事
- **50% 折扣**：缓存命中的 input token 按半价计费
- **1,024 token 最低门槛**：短于这个阈值的请求不触发缓存
- **~5-10 分钟 TTL**：不活跃后缓存过期

#### 响应中如何判断缓存命中

```json
{
  "usage": {
    "prompt_tokens": 500,
    "prompt_tokens_details": {
      "cached_tokens": 23000  // 这些 token 按半价计费
    }
  }
}
```

### 2.4 Google Gemini：双模式缓存

#### 隐式缓存（自动）

与 OpenAI 类似，自动检测重复前缀并缓存，无需开发者干预。

#### 显式缓存（Cached Contents API）

Gemini 独有的能力——可以通过 API **显式创建、管理和删除缓存**：

```python
# 创建显式缓存
cached_content = client.caches.create(
    model="gemini-2.5-pro",
    contents=[
        {"role": "user", "parts": [{"text": "长文档内容..."}]}
    ],
    ttl="3600s",  # 自定义 TTL
)

# 使用缓存
response = client.generate_content(
    model="gemini-2.5-pro",
    contents="基于缓存内容回答问题",
    cached_content=cached_content.name,
)
```

#### 特点

- 最高 **75%** 折扣
- **32,768 token** 最低门槛（显式缓存）
- **可配置 TTL**：从几分钟到几小时
- **开发者控制度最高**：可以主动管理缓存生命周期

### 2.5 DeepSeek：全自动 + 高折扣

#### 工作方式

与 OpenAI 类似，全自动缓存，无需开发者干预。

#### 特点

- 最高 **90%** 折扣（与 Anthropic 并列最高）
- **~1,024 token** 最低门槛
- 文档化程度较低，但社区已广泛确认

---

## 三、为什么 Anthropic 选择手动控制？

### 3.1 核心权衡：最高折扣 vs 最低门槛

```
OpenAI 策略：  低折扣(50%) + 零配置 → 门槛最低，但省得少
Anthropic 策略：高折扣(90%) + 手动标记 → 省得多，但需要开发者理解缓存
Gemini 策略：  中折扣(75%) + 灵活控制 → 平衡方案
```

**90% 的折扣不是白给的。** Anthropic 要求开发者明确告诉 API "哪些内容值得缓存"，避免服务端资源浪费。

### 3.2 缓存本身有成本

每次放置 `cache_control` 标记，服务端都要：

1. **写入 KV Cache 到持久存储**：I/O 成本
2. **占用存储空间**：2 万 token 的 KV Cache 可能有几百 MB
3. **维护缓存一致性**：TTL 管理、清理等

如果所有请求都自动缓存，大量一次性请求（只调用一次的 API）的缓存永远不会被复用，**纯粹浪费存储资源**。

OpenAI 用 **低折扣**（50%）抑制滥用——缓存命中省得不多，服务端有更多余量。Anthropic 用 **高折扣**（90%）激励正确使用，但要求开发者明确标记。

### 3.3 手动控制带来的三个关键能力

#### 能力 1：精确控制缓存边界

```
自动缓存的问题：
  请求：[静态部分 A] [动态部分 B] [工具定义] [消息历史]
  结果：整个前缀一起缓存 → B 一变，全部失效

手动标记的解法：
  [静态部分 A ✓标记] [动态部分 B] [工具定义] [消息历史 ✓标记]
  结果：A 被独立缓存，B 变化不影响 A 的缓存命中
```

Claude Code 正是这样做的——用 `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` 将静态和动态部分拆开，静态部分用 `global` scope 标记，10 万用户共享一份缓存。

#### 能力 2：控制缓存作用域

```
global scope：10 万用户发送相同的 2 万 token 静态 System Prompt
→ 服务端只存 1 份 KV Cache
→ 所有用户共享

自动缓存：每个用户的缓存是独立的
→ 10 万用户 = 10 万份 KV Cache
→ 存储浪费
```

#### 能力 3：控制缓存写入位置

```
Fork 子进程：[父线程历史] [fork 指令]

自动缓存：fork 的末尾消息也被缓存 → 污染缓存池
手动 skipCacheWrite：标记放在倒数第二条 → fork 不写入末尾
→ 避免临时消息污染缓存
```

### 3.4 "字节完全一致"的代价

手动标记的代价是**开发者需要确保前缀字节完全一致**。Claude Code 源码中为此做了大量防御性设计：

| 防御措施 | 防什么 |
|---------|-------|
| `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` | 动态内容变化拖垮整个缓存 |
| `systemPromptSection()` vs `DANGEROUS_uncachedSystemPromptSection()` | 防止 section 重新计算导致字节变化 |
| `toolSchemaCache` | 防止工具定义描述抖动 |
| Latch 锁存模式 | 防止 TTL/feature flag 中途翻转 |
| Fork Agent byte-exact 传递 | 防止重新生成 System Prompt 导致字节差异 |
| `contentReplacementState` 克隆 | 防止 fork 中工具结果替换决策不一致 |

**一个空格的差异就能让缓存失效，这就是手动控制的认知负担。**

#### 防御措施详解：Tool Schema Cache

工具定义（Tool Schema）位于 System Prompt 之后、消息历史之前。如果工具定义的字节在两次请求之间发生了变化，从 tools 开始后面的缓存全部失效——包括后面的消息历史缓存。

```
请求1: [system(缓存命中)] [tools: AAAAA] [messages(缓存命中)]
                                  ↑
                           字节: AAAAA

请求2: [system(缓存命中)] [tools: AAAAB] [messages(???)]
                                  ↑
                           字节: AAAAB   ← 一个字节不同
                                  ↑
                           从 tools 开始缓存全部失效！
                           之前命中的 messages 缓存也作废！
```

字节抖动的来源：

1. **description 包含动态内容**：工具描述里拼接了当前时间、工作目录等，每次不同
2. **字典序列化顺序不确定**：schema 里有未排序的子结构时，JSON 序列化可能不同
3. **浮点数精度**：schema 里的 default 值如果包含浮点数，序列化结果可能有微小差异

解决方案是 **Session 级缓存**——同一个 session 内，工具定义只序列化一次，之后永远复用同一份：

```python
class ToolRegistry:
    def __init__(self):
        self._tools = {}
        self._schema_cache = {}  # session 级别，只算一次

    def to_api_format(self, family="openai"):
        result = []
        for name, tool in self._tools.items():
            if name not in self._schema_cache:
                self._schema_cache[name] = tool.get_schema()  # 只序列化一次
            result.append(self._schema_cache[name])           # 之后复用同一份
        return result
```

同一个 session 内，`to_api_format()` 永远返回同一块内存的引用，字节不可能不同。

#### 防御措施详解：Latch 锁存模式

GrowthBook 等特性开关平台会在 session 中途更新实验分组，导致 `cache_control` 的 `scope` 或 `ttl` 字段变化：

```
T=0:00  用户开始对话
        GrowthBook 返回: prompt_cache_1h = false
        → cache_control = {"type": "ephemeral"}           ← 5 分钟 TTL

T=2:00  第 2 轮请求
        GrowthBook 返回: prompt_cache_1h = false         ← 没变
        → cache_control = {"type": "ephemeral"}           ← 字节一致，缓存命中 ✓

T=3:30  运营人员调整了 GrowthBook，用户被分到实验组
        GrowthBook 返回: prompt_cache_1h = true           ← 变了！
        → cache_control = {"type": "ephemeral", "ttl": "1h"}  ← 多了 "ttl":"1h"
        字节不一致！缓存全部失效！❌
```

更糟糕的是来回翻转：

```
T=0  → ttl=false → {"type":"ephemeral"}              → 缓存建立
T=3  → ttl=true  → {"type":"ephemeral","ttl":"1h"}   → 缓存失效！重新建立
T=6  → ttl=false → {"type":"ephemeral"}              → 又失效！又重新建立
T=9  → ttl=true  → {"type":"ephemeral","ttl":"1h"}   → 又失效！
```

Latch 模式的解决方式——首次读取后锁定，整个 session 内不再改变：

```python
class CacheControlConfig:
    @property
    def effective_scope(self):
        if self._latched_scope is None:
            self._latched_scope = self._scope  # 锁定！
        return self._latched_scope              # 之后不再改变
```

```
T=0  → 首次读取: ttl=false → latch 锁定 false
T=3  → GrowthBook 说 ttl=true → 但 latch 锁住了，effective_ttl_1h 仍然 false
        → cache_control = {"type": "ephemeral"}         → 字节不变，缓存命中 ✓
T=6  → GrowthBook 说 ttl=true → latch 仍锁住
        → cache_control = {"type": "ephemeral"}         → 继续命中 ✓
```

牺牲的是：用户虽然被分到了 1 小时 TTL 的实验组，但这个 session 内仍然用 5 分钟 TTL。代价很小（5 分钟对正常使用足够），换来的是缓存命中率正常。等 session 结束（`/clear` 或新对话），latch 重置，下个 session 就会用新的配置。

---

## 四、如何选择：不同场景下的缓存策略

### 4.1 决策矩阵

| 场景 | 推荐提供商 | 理由 |
|------|-----------|------|
| 多轮对话 CLI（如 Claude Code） | Anthropic | 长前缀 + 高频重复 → 90% 折扣收益巨大 |
| 简单 Chatbot | OpenAI | 零配置，50% 折扣已足够 |
| 大规模 RAG 应用 | Gemini | Cached Contents API 可预缓存文档 |
| 成本敏感的长上下文任务 | DeepSeek | 90% 折扣 + 零配置 |
| 多用户共享 System Prompt 的 SaaS | Anthropic | global scope 跨用户共享缓存 |

### 4.2 通用缓存最佳实践

无论使用哪个提供商，以下实践都能提升缓存命中率：

#### 1. 保持前缀稳定

```python
# 好：System Prompt 在最前面，内容不变
messages = [
    {"role": "system", "content": SYSTEM_PROMPT},  # 固定
    {"role": "user", "content": user_input},        # 变化
]

# 差：动态内容混在前面
messages = [
    {"role": "system", "content": f"今天是{date}..."},  # 每次不同
    {"role": "user", "content": user_input},
]
```

#### 2. 静态和动态内容分离

```python
# Anthropic: 用不同的 cache_control 标记
system_blocks = [
    # 静态部分 → 缓存
    {"type": "text", "text": static_content,
     "cache_control": {"type": "ephemeral", "scope": "global"}},
    # 动态部分 → 不缓存
    {"type": "text", "text": dynamic_content},
]

# OpenAI/Gemini: 把不变的内容放在最前面
system_message = f"{STATIC_INSTRUCTIONS}\n\n{dynamic_context}"
```

#### 3. 避免不必要的重排序

```python
# 好：工具定义顺序固定
tools = [read_tool, edit_tool, bash_tool]  # 永远不变

# 差：每次请求工具顺序不同
tools = sorted(all_tools, key=lambda t: t.name)  # 新工具加入后顺序变化
```

#### 4. 锁存影响缓存的配置

```python
# 不好：每次请求重新评估 feature flag
if feature_flag_enabled("new_prompt"):
    system += "额外指令"

# 好：session 开始时评估一次并缓存
_new_prompt_enabled = None

def get_system_prompt():
    global _new_prompt_enabled
    if _new_prompt_enabled is None:
        _new_prompt_enabled = feature_flag_enabled("new_prompt")
    if _new_prompt_enabled:
        return BASE_PROMPT + EXTRA_PROMPT
    return BASE_PROMPT
```

---

## 五、Anthropic Prompt Cache 快速上手

### 5.1 最简实现（3 步）

```python
import anthropic

client = anthropic.Anthropic()

# 第 1 步：System Prompt 用列表格式 + cache_control
system = [
    {
        "type": "text",
        "text": "你是一个编程助手，请用中文回答。",
        "cache_control": {"type": "ephemeral"}  # ← 标记缓存
    }
]

# 第 2 步：消息中也可以标记
messages = [
    {"role": "user", "content": "解释 Python 的装饰器"},
]

# 第 3 步：正常调用 API
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system=system,
    messages=messages,
)

# 查看缓存统计
print(f"缓存写入: {response.usage.cache_creation_input_tokens} tokens")
print(f"缓存读取: {response.usage.cache_read_input_tokens} tokens")
print(f"正常输入: {response.usage.input_tokens} tokens")
```

### 5.2 多轮对话缓存优化

```python
messages = []

for turn in range(10):
    # 用户输入
    user_msg = {"role": "user", "content": f"第 {turn+1} 个问题..."}
    messages.append(user_msg)

    # 在最后一条消息上标记 cache_control
    # 注意：只有最后一条需要标记，前面的都会被自动缓存
    if isinstance(messages[-1]["content"], str):
        messages[-1]["content"] = [
            {
                "type": "text",
                "text": messages[-1]["content"],
                "cache_control": {"type": "ephemeral"}  # ← 标记末尾
            }
        ]

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system,  # 带缓存标记的 system prompt
        messages=messages,
    )

    # 检查缓存效果
    if response.usage.cache_read_input_tokens > 0:
        saved = response.usage.cache_read_input_tokens * 0.9  # 省了 90%
        print(f"缓存命中！节省约 {saved} token 成本")

    # 记录助手回复
    assistant_text = response.content[0].text
    messages.append({"role": "assistant", "content": assistant_text})
```

### 5.3 静态/动态分离的完整示例

```python
# 静态部分：所有用户共享，用 global scope
static_system = {
    "type": "text",
    "text": """你是一个编程助手。
请遵循以下规则：
1. 使用 Python
2. 添加类型注解
3. 编写单元测试""",
    "cache_control": {"type": "ephemeral", "scope": "global"}  # 跨用户共享
}

# 动态部分：每个用户/会话不同，不缓存
def build_dynamic_system(cwd: str, git_branch: str) -> dict:
    return {
        "type": "text",
        "text": f"当前目录: {cwd}\nGit 分支: {git_branch}",
        # 不加 cache_control → 不缓存
    }

# 组合
system = [
    static_system,
    build_dynamic_system("/home/user/project", "main"),
]

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system=system,
    messages=[{"role": "user", "content": "帮我重构这个函数"}],
)
```

---

## 六、总结

| | Anthropic | OpenAI | Gemini | DeepSeek |
|---|---|---|---|---|
| 折扣 | 90% | 50% | 75% | 90% |
| 配置 | 手动标记 | 零配置 | 双模式 | 零配置 |
| 控制度 | 高 | 低 | 中高 | 低 |
| 适合场景 | 长前缀+高频重复 | 简单应用 | RAG/文档缓存 | 成本敏感 |

**Anthropic 的手动缓存本质是一个工程权衡**：

```
高折扣(90%) + 手动控制 + 字节一致性约束
         ⇕
低折扣(50%) + 零配置 + 服务端自动处理
```

选择哪条路取决于你的场景——如果你的应用有长的、重复的前缀（如 CLI 工具、Agent 框架），Anthropic 的 90% 折扣收益巨大，值得投入精力做缓存优化。如果你的应用请求模式简单，OpenAI/DeepSeek 的自动缓存足够用。

---

*参考文档：[claude-code-docs/07-Prompt-Cache.md](../claude-code-docs/07-Prompt-Cache.md)*