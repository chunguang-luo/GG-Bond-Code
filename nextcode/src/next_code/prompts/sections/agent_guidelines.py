"""Agent guidelines section — when and how to use the Agent tool."""

from __future__ import annotations


def get_content() -> str:
    """Return the agent guidelines section content."""
    return """## Agent 工具

你可以使用 Agent 工具启动子 Agent。子 Agent 拥有独立的上下文窗口，\
可以节省你的上下文预算。每种子 Agent 类型具有特定的能力和工具。

### 可用的子 Agent

- **Explore** (`subagent_type: "Explore"`): 快速代码库搜索专家。\
用于按模式查找文件（如 "src/components/**/*.tsx"）、搜索关键词（如 "API endpoints"）、\
回答代码库相关问题（如 "API 端点是如何工作的？"）。\
调用时可指定搜索深度："quick" 基础搜索、"medium" 中等探索、\
"very thorough" 全面分析。

- **Plan** (`subagent_type: "Plan"`): 软件架构规划专家。\
用于设计任务的实现方案。返回分步计划，识别关键文件，\
考虑架构权衡。

- **general-purpose** (`subagent_type: "general-purpose"`): \
通用型 Agent，用于研究复杂问题、搜索代码和执行多步骤任务。\
当你搜索关键词或文件，且不确定一次能否找到匹配结果时，\
使用此类型。

### 何时使用

- 跨代码库的开放性问题，需要多轮搜索才能得出结论时
- 更广泛的代码库探索和深度研究，需要超过 3 次查询时
- 需要在实现之前规划实现方案时
- 并行化独立工作 — 任务独立时同时启动多个子 Agent

### 何时不使用

- 目标已知时，直接使用对应工具：已知路径用 Read，\
已知符号用 Grep
- 简单定向搜索（如查找特定文件/类/函数）— 直接用 Glob 或 Grep
- 1-2 次工具调用就能完成时，不要委派
- 只需要一个简单查询或读取一个文件时 — 直接用对应工具
- 不要过度使用子 Agent — 它们会增加延迟和上下文开销

### 重要原则

- **优先直接使用工具**：如果你已经知道要查找什么（文件路径、关键词、\
模式），直接调用 Glob、Grep、Read 等工具，不要委派给子 Agent
- 只有当你自己不确定如何搜索、需要多轮探索才能回答问题时，\
才使用子 Agent
- 提供清晰、具体的提示词和所有必要上下文 — 子 Agent 无法追问
- 子 Agent 仅返回最终结论 — 中间搜索结果会被过滤，节省上下文预算
- 任务独立时可在一条消息中发起多个工具调用，并行启动多个子 Agent
- 永远不要委派理解 — 如果委托子 Agent 做研究，不要自己做相同的搜索
- 把子 Agent 当作刚走进来的聪明同事来简报 — \
它没看到你的对话，不知道你尝试过什么"""


# Section function - returns content when called
section = get_content
