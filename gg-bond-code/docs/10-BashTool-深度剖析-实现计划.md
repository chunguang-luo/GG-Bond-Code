# BashTool 深度剖析 - 实现计划

> 基于原文档：`claude-code-docs/10-BashTool-深度剖析.md`

## 文档核心要点总结

### 核心概念
- **纵深防御**：BashTool 采用多层安全防线（输入验证 → AST 解析 → 语义检查 → 权限规则 → 路径约束 → 只读验证 → 沙箱），任何一层都能阻止危险操作
- **Fail-Closed 白名单**：安全判定使用白名单而非黑名单，未知结构默认拒绝/询问用户
- **命令语义分类**：将命令分为搜索/读取/列表/静默/破坏性等类别，驱动差异化 UI 展示和安全策略
- **AST 优先 + Legacy 回退**：tree-sitter AST 解析是主安全入口，不可用时回退到正则分析
- **只读命令白名单**：100+ 命令的安全标志配置，精确到每个 flag 的安全/危险判定
- **沙箱执行**：文件系统读写限制 + 网络白名单 + 环境变量/包装器迭代剥离
- **智能规则建议**：用户批准后自动提取可复用的权限前缀规则
- **退出码语义**：grep/diff/test 等命令的非零退出码信息解读

### 关键设计模式
1. **纵深防御架构**：多维度安全检查按优先级编排，任何维度可独立阻止
2. **Fail-Closed 白名单模式**：只允许已知安全项通过，未知一律拒绝
3. **语义分类驱动的差异化处理**：按命令语义分类选择不同的 UI/安全策略

---

## 项目当前实现分析

### 相关代码文件
| 文件 | 行数 | 当前实现 |
|------|------|---------|
| `tools/bash.py` | 54 | 最小化实现：只有 `execute()` 执行命令 + 超时处理 |
| `tools/base.py` | 174 | Tool ABC 含 `is_read_only()`、`is_concurrency_safe()` |
| `permissions/manager.py` | 122 | 简单 allow/deny/ask 判定 + session grants + `is_read_only()` 自动放行 |
| `permissions/__init__.py` | 1 | 空模块 |
| `tools/streaming_executor.py` | 280 | 工具执行编排，含权限检查回调 |
| `prompts/sections/tool_guidelines.py` | 32 | 工具偏好引导（Read 优先于 cat 等） |

### 完成度评估

| 功能点 | 文档要求 | 当前实现 | 状态 | 差距 |
|--------|---------|---------|------|------|
| 命令执行 | `runShellCommand` AsyncGenerator | 简单 `asyncio.create_subprocess_shell` | 🔴 部分 | 无进度输出、无前后台切换、无大输出持久化 |
| 输入验证 | sleep 检测 + 基本合法性 | 无 | 🔴 未实现 | 完全缺失 |
| AST 解析 | tree-sitter + fail-closed | 无 | 🔴 未实现 | 完全缺失 |
| 安全检查 | 23 种安全模式检测 | 无 | 🔴 未实现 | 完全缺失 |
| 只读命令验证 | 100+ 命令白名单 + 安全标志 | 硬编码 `is_read_only()=False` | 🔴 未实现 | BashTool 永远返回 False |
| 权限规则匹配 | exact/prefix/wildcard 三种形态 | 简单 fnmatch glob 匹配 | 🟡 部分 | 缺少前缀/通配符的语义区分和智能建议 |
| 环境变量/包装器剥离 | 安全变量白名单 + 迭代剥离 | 无 | 🔴 未实现 | 完全缺失 |
| 智能规则建议 | 前缀提取 + 子命令验证 | 无 | 🔴 未实现 | 完全缺失 |
| 沙箱执行 | 文件系统限制 + 网络白名单 | 无 | 🔴 未实现 | 完全缺失 |
| 退出码语义 | grep/diff/test 退出码解读 | 统一 returncode 判断 | 🔴 未实现 | 所有非零退出码都视为错误 |
| 命令语义分类 | 搜索/读取/列表/静默分类 | 无 | 🔴 未实现 | 完全缺失 |
| sed 编辑特殊处理 | sed -i 解析 → 预览 → 精确写入 | 无 | 🔴 未实现 | 完全缺失 |
| 破坏性命令警告 | git reset --hard 等模式检测 | 无 | 🔴 未实现 | 完全缺失 |
| 进度显示 | 2 秒阈值 + 实时输出流 | 无 | 🔴 未实现 | 完全缺失 |
| Prompt 工程引导 | 工具偏好 + Git 安全协议 | 已有 `tool_guidelines.py` | 🟢 基本 | 缺少 Git 安全协议、沙箱配置注入 |

---

## 差异对比分析

### 架构层面

| 维度 | 文档要求（18 文件 / 12,400 行） | 当前实现（1 文件 / 54 行） | 差异等级 | 备注 |
|------|------|------|----------|------|
| 安全架构 | 纵深防御 7 层 | 无安全层 | 🔴 高 | 核心架构缺失 |
| 命令解析 | tree-sitter AST + 正则回退 | 无解析 | 🔴 高 | 需要 Python 等价方案 |
| 权限系统 | exact/prefix/wildcard + 智能建议 | 简单 glob 匹配 | 🟡 中 | 基础可用但缺乏智能性 |
| 执行引擎 | AsyncGenerator + 进度 + 前后台 | 简单 subprocess | 🔴 高 | 核心执行能力不足 |
| 只读判定 | 100+ 命令配置 + 安全标志验证 | 硬编码 False | 🔴 高 | BashTool 永远需确认 |

### 功能层面

| 维度 | 文档要求 | 当前实现 | 差异等级 | 备注 |
|------|---------|---------|----------|------|
| 退出码语义 | 语义映射表 | 非0=错误 | 🔴 高 | AI 误判 grep 无匹配为错误 |
| 输出处理 | 持久化 + 图片检测 + 截断 | 直接返回 | 🟡 中 | 大输出可能 OOM |
| sed 特殊处理 | 解析→预览→精确写入 | 无 | 🟡 低 | 可选优化 |
| 沙箱 | 文件系统+网络限制 | 无 | 🔴 高 | 生产环境必需 |

### 性能层面

| 维度 | 文档要求 | 当前实现 | 差异等级 | 备注 |
|------|---------|---------|----------|------|
| 复合命令上限 | 50 子命令上限 | 无限制 | 🟡 中 | 潜在 DoS 风险 |
| 大输出处理 | 64MB 上限 + 文件模式 | 全量内存 | 🟡 中 | 潜在 OOM |
| 进度延迟 | 2 秒阈值避免闪烁 | 无进度 | 🟢 低 | UX 优化 |

---

## 实现方案

### 阶段总览

| 阶段 | 名称 | 优先级 | 预计文件数 | 核心目标 |
|------|------|--------|-----------|---------|
| P0-1 | 命令语义分类 + 退出码解读 | P0 | 3 新建 / 1 修改 | BashTool 智能化的基础 |
| P0-2 | 安全检查系统 | P0 | 3 新建 / 1 修改 | 核心安全防线 |
| P0-3 | 只读命令验证 | P0 | 2 新建 / 2 修改 | 减少 BashTool 权限弹窗 |
| P1-1 | 智能权限规则 | P1 | 2 新建 / 1 修改 | 提升权限管理体验 |
| P1-2 | 执行引擎增强 | P1 | 1 新建 / 1 修改 | 进度输出 + 前后台切换 |
| P2-1 | 沙箱执行 | P2 | 2 新建 / 1 修改 | 运行时隔离 |
| P2-2 | sed 编辑特殊处理 | P2 | 1 新建 / 1 修改 | 编辑预览 + TOCTOU 修复 |

---

### P0-1：命令语义分类 + 退出码解读

**目标**：让 BashTool 理解命令的语义类型和退出码含义，为后续安全策略和 UI 展示奠定基础。

**新建文件**：
1. `gg-bond-code/src/gg_bond_code/tools/bash_semantics.py` — 命令语义分类
2. `gg-bond-code/src/gg_bond_code/tools/bash_exit_codes.py` — 退出码语义解释

**修改文件**：
1. `gg-bond-code/src/gg_bond_code/tools/bash.py` — 集成语义分类和退出码解读

**详细任务**：

#### 任务 1：命令语义分类 (`bash_semantics.py`)

```python
"""Command semantic classification for BashTool.

Mirrors Claude Code's BASH_SEARCH_COMMANDS / BASH_READ_COMMANDS /
BASH_LIST_COMMANDS / BASH_SILENT_COMMANDS sets.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any


class CommandSemantic(Enum):
    """Semantic category of a bash command."""
    SEARCH = "search"      # find, grep, rg, ag, ...
    READ = "read"          # cat, head, tail, wc, ...
    LIST = "list"          # ls, tree, du
    SILENT = "silent"      # mv, cp, rm, mkdir, ... (no stdout on success)
    NEUTRAL = "neutral"    # echo, printf, true, false, :
    DESTRUCTIVE = "destructive"  # Any command not in safe sets
    UNKNOWN = "unknown"


# --- Command sets (mirrors BashTool.tsx:60-72) ---

BASH_SEARCH_COMMANDS: set[str] = {
    "find", "grep", "rg", "ag", "ack", "locate", "which", "whereis",
}

BASH_READ_COMMANDS: set[str] = {
    "cat", "head", "tail", "less", "more",
    "wc", "stat", "file", "strings",
    "jq", "awk", "cut", "sort", "uniq", "tr",
}

BASH_LIST_COMMANDS: set[str] = {"ls", "tree", "du"}

BASH_SILENT_COMMANDS: set[str] = {
    "mv", "cp", "rm", "mkdir", "rmdir", "chmod", "chown",
    "chgrp", "touch", "ln", "cd", "export", "unset", "wait",
}

BASH_NEUTRAL_COMMANDS: set[str] = {
    "echo", "printf", "true", "false", ":",
}


def _extract_base_command(token: str) -> str:
    """Extract the base command name from a token.

    Handles paths like /usr/bin/git -> git.
    """
    return token.rsplit("/", maxsplit=1)[-1] if "/" in token else token


def classify_command(command: str) -> CommandSemantic:
    """Classify a single command (no pipes/&&) by semantic category.

    Returns the most specific category for the command.
    """
    stripped = command.strip()
    if not stripped:
        return CommandSemantic.UNKNOWN

    # Split to get the first token (base command)
    tokens = stripped.split()
    if not tokens:
        return CommandSemantic.UNKNOWN

    base = _extract_base_command(tokens[0])

    if base in BASH_SEARCH_COMMANDS:
        return CommandSemantic.SEARCH
    elif base in BASH_READ_COMMANDS:
        return CommandSemantic.READ
    elif base in BASH_LIST_COMMANDS:
        return CommandSemantic.LIST
    elif base in BASH_SILENT_COMMANDS:
        return CommandSemantic.SILENT
    elif base in BASH_NEUTRAL_COMMANDS:
        return CommandSemantic.NEUTRAL
    else:
        return CommandSemantic.UNKNOWN


def classify_pipeline(command: str) -> CommandSemantic:
    """Classify a full command pipeline.

    A pipeline is considered collapsible (search/read/list) only if
    ALL non-neutral sub-commands belong to the same collapsible category.

    Mirrors isSearchOrReadBashCommand() in BashTool.tsx:59-172.
    """
    # Split on pipe operators (simplified — doesn't handle quotes)
    parts = re.split(r"\s*\|\s*", command)

    collapsible_categories: set[CommandSemantic] = set()

    for part in parts:
        # Further split on && and ||
        subcmds = re.split(r"\s*(?:&&|\|\|)\s*", part)
        for subcmd in subcmds:
            subcmd = subcmd.strip()
            if not subcmd:
                continue
            sem = classify_command(subcmd)
            if sem == CommandSemantic.NEUTRAL:
                continue  # Neutral commands don't affect classification
            if sem in (CommandSemantic.SEARCH, CommandSemantic.READ, CommandSemantic.LIST):
                collapsible_categories.add(sem)
            else:
                # Any non-collapsible, non-neutral command -> DESTRUCTIVE
                return CommandSemantic.DESTRUCTIVE

    if not collapsible_categories:
        # All commands were neutral or empty
        return CommandSemantic.NEUTRAL

    # If all collapsible commands are in the same category, use that
    if len(collapsible_categories) == 1:
        return collapsible_categories.pop()

    # Mixed collapsible categories -> default to the first one found
    # (In Claude Code, search takes precedence)
    if CommandSemantic.SEARCH in collapsible_categories:
        return CommandSemantic.SEARCH

    return CommandSemantic.UNKNOWN


def is_silent_command(command: str) -> bool:
    """Check if a command typically produces no stdout on success."""
    base = command.strip().split()[0] if command.strip() else ""
    base = _extract_base_command(base)
    return base in BASH_SILENT_COMMANDS
```

#### 任务 2：退出码语义解释 (`bash_exit_codes.py`)

```python
"""Exit code semantic interpretation for BashTool.

Mirrors commandSemantics.ts — many commands use non-zero exit codes
to convey information rather than errors (e.g., grep returns 1 when
no matches are found, not because of an actual error).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class ExitCodeSemantic:
    """Semantic interpretation of an exit code."""
    is_error: bool
    message: str | None = None


# Type for semantic interpreter functions
SemanticInterpreter = Callable[[int], ExitCodeSemantic]


def _grep_semantic(exit_code: int) -> ExitCodeSemantic:
    """grep: 0=found, 1=not found, 2+=error."""
    if exit_code == 0:
        return ExitCodeSemantic(is_error=False)
    if exit_code == 1:
        return ExitCodeSemantic(is_error=False, message="No matches found")
    return ExitCodeSemantic(is_error=True)


def _diff_semantic(exit_code: int) -> ExitCodeSemantic:
    """diff: 0=same, 1=different, 2+=error."""
    if exit_code == 0:
        return ExitCodeSemantic(is_error=False)
    if exit_code == 1:
        return ExitCodeSemantic(is_error=False, message="Files differ")
    return ExitCodeSemantic(is_error=True)


def _test_semantic(exit_code: int) -> ExitCodeSemantic:
    """test/[: 0=true, 1=false, 2+=error."""
    if exit_code <= 1:
        return ExitCodeSemantic(is_error=False, message="Condition is false" if exit_code == 1 else None)
    return ExitCodeSemantic(is_error=True)


def _git_semantic(exit_code: int) -> ExitCodeSemantic:
    """git: many subcommands return 1 for expected conditions."""
    if exit_code == 0:
        return ExitCodeSemantic(is_error=False)
    if exit_code == 1:
        return ExitCodeSemantic(is_error=False, message="Command returned 1 (expected condition)")
    return ExitCodeSemantic(is_error=True)


def _default_semantic(exit_code: int) -> ExitCodeSemantic:
    """Default: non-zero = error."""
    return ExitCodeSemantic(is_error=exit_code != 0)


# Registry of command-specific exit code interpreters
COMMAND_SEMANTICS: dict[str, SemanticInterpreter] = {
    "grep": _grep_semantic,
    "egrep": _grep_semantic,
    "fgrep": _grep_semantic,
    "rg": _grep_semantic,  # ripgrep follows same convention
    "ag": _grep_semantic,  # silver searcher follows same convention
    "diff": _diff_semantic,
    "test": _test_semantic,
    "[": _test_semantic,
    "[[": _test_semantic,
    "git": _git_semantic,
}


def interpret_exit_code(command: str, exit_code: int) -> ExitCodeSemantic:
    """Interpret the exit code of a command semantically.

    Args:
        command: The command that was executed.
        exit_code: The exit code returned.

    Returns:
        ExitCodeSemantic with is_error flag and optional message.
    """
    # Extract base command
    base = command.strip().split()[0] if command.strip() else ""
    if "/" in base:
        base = base.rsplit("/", maxsplit=1)[1]

    interpreter = COMMAND_SEMANTICS.get(base, _default_semantic)
    return interpreter(exit_code)
```

#### 任务 3：集成到 BashTool (`bash.py` 修改)

关键改动：
- `execute()` 中使用 `interpret_exit_code()` 替代简单的 `returncode != 0` 判断
- `is_read_only()` 使用 `classify_pipeline()` 作为初步只读判定（后续 P0-3 将替换为完整验证）
- 添加 `description` 参数到 schema

---

### P0-2：安全检查系统

**目标**：实现 BashTool 的核心安全防线，防止危险命令执行。

**新建文件**：
1. `gg-bond-code/src/gg_bond_code/tools/bash_security.py` — 安全模式检测（23 种检查的 Python 版本）
2. `gg-bond-code/src/gg_bond_code/tools/bash_input_validation.py` — 输入验证

**修改文件**：
1. `gg-bond-code/src/gg_bond_code/tools/bash.py` — 集成安全检查到执行流程

**详细任务**：

#### 任务 1：安全模式检测 (`bash_security.py`)

核心检查项（按优先级排序）：

```python
"""Bash command security analysis.

Mirrors bashSecurity.ts — detects dangerous patterns in shell commands.
Uses regex-based analysis as the primary method (Python doesn't have
a tree-sitter-bash equivalent that's readily available).

Key design: FAIL-CLOSED — if we can't determine a command is safe,
we mark it as needing user confirmation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class SecurityCheckID(Enum):
    """Security check identifiers (mirrors BASH_SECURITY_CHECK_IDS)."""
    INCOMPLETE_COMMANDS = 1
    JQ_SYSTEM_FUNCTION = 2
    OBFUSCATED_FLAGS = 4
    SHELL_METACHARACTERS = 5
    DANGEROUS_VARIABLES = 6
    NEWLINES = 7
    IFS_INJECTION = 11
    PROC_ENVIRON_ACCESS = 13
    MALFORMED_TOKEN_INJECTION = 14
    BRACE_EXPANSION = 16
    CONTROL_CHARACTERS = 17
    UNICODE_WHITESPACE = 18
    ZSH_DANGEROUS_COMMANDS = 20
    COMMENT_QUOTE_DESYNC = 22


@dataclass
class SecurityCheckResult:
    """Result of security analysis."""
    is_safe: bool = True
    check_id: SecurityCheckID | None = None
    message: str = ""
    details: list[str] = field(default_factory=list)


# --- Command substitution patterns (mirrors bashSecurity.ts:16-41) ---

COMMAND_SUBSTITUTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"<\("), "process substitution <()"),
    (re.compile(r">\("), "process substitution >()"),
    (re.compile(r"=\("), "Zsh process substitution =()"),
    (re.compile(r"(?:^|[\s;&|])=[a-zA-Z_]"), "Zsh equals expansion (=cmd)"),
    (re.compile(r"\$\("), "$() command substitution"),
    (re.compile(r"\$\{"), "${} parameter substitution"),
    (re.compile(r"\$\["), "$[] legacy arithmetic expansion"),
]

# --- Zsh dangerous commands (mirrors bashSecurity.ts:45-74) ---

ZSH_DANGEROUS_COMMANDS: set[str] = {
    "zmodload", "emulate",
    "sysopen", "sysread", "syswrite", "sysseek",
    "zpty", "ztcp", "zsocket",
    "zf_rm", "zf_mv", "zf_ln", "zf_chmod",
}

# --- Dangerous shell builtins ---

DANGEROUS_BUILTINS: set[str] = {
    "eval", "exec", "source", ".",  # Code execution
    "kill", "trap",                 # Signal handling
    "unset", "export",              # Environment manipulation (export is borderline)
}

# --- Bare shell prefixes (never suggest as permission rule prefix) ---

BARE_SHELL_PREFIXES: set[str] = {
    "sh", "bash", "zsh", "fish", "csh", "ksh", "dash",
    "env", "xargs",
    "nice", "stdbuf", "nohup", "timeout", "time",
    "sudo", "doas", "pkexec",
}


def check_command_substitution(command: str) -> SecurityCheckResult:
    """Check for command/parameter substitution patterns."""
    for pattern, message in COMMAND_SUBSTITUTION_PATTERNS:
        if pattern.search(command):
            return SecurityCheckResult(
                is_safe=False,
                check_id=SecurityCheckID.SHELL_METACHARACTERS,
                message=f"Dangerous pattern detected: {message}",
            )
    return SecurityCheckResult(is_safe=True)


def check_zsh_dangerous_commands(command: str) -> SecurityCheckResult:
    """Check for Zsh-specific dangerous commands."""
    tokens = command.strip().split()
    if not tokens:
        return SecurityCheckResult(is_safe=True)

    base = tokens[0].rsplit("/", 1)[-1] if "/" in tokens[0] else tokens[0]

    if base in ZSH_DANGEROUS_COMMANDS:
        return SecurityCheckResult(
            is_safe=False,
            check_id=SecurityCheckID.ZSH_DANGEROUS_COMMANDS,
            message=f"Zsh dangerous command: {base}",
        )
    return SecurityCheckResult(is_safe=True)


def check_dangerous_builtins(command: str) -> SecurityCheckResult:
    """Check for dangerous shell builtins."""
    tokens = command.strip().split()
    if not tokens:
        return SecurityCheckResult(is_safe=True)

    base = tokens[0].rsplit("/", 1)[-1] if "/" in tokens[0] else tokens[0]

    if base in DANGEROUS_BUILTINS:
        return SecurityCheckResult(
            is_safe=False,
            check_id=SecurityCheckID.SHELL_METACHARACTERS,
            message=f"Dangerous builtin: {base}",
        )
    return SecurityCheckResult(is_safe=True)


def check_newlines(command: str) -> SecurityCheckResult:
    """Check for newlines that may hide subsequent commands."""
    if "\n" in command:
        return SecurityCheckResult(
            is_safe=False,
            check_id=SecurityCheckID.NEWLINES,
            message="Command contains newlines (may hide subsequent commands)",
        )
    return SecurityCheckResult(is_safe=True)


def check_control_characters(command: str) -> SecurityCheckResult:
    """Check for control characters."""
    for char in command:
        if ord(char) < 0x20 and char not in ("\n", "\t"):
            return SecurityCheckResult(
                is_safe=False,
                check_id=SecurityCheckID.CONTROL_CHARACTERS,
                message="Command contains control characters",
            )
    return SecurityCheckResult(is_safe=True)


def check_unicode_whitespace(command: str) -> SecurityCheckResult:
    """Check for Unicode whitespace that may bypass simple parsing."""
    for i, char in enumerate(command):
        if char in ("\u00a0", "\u2000", "\u2001", "\u2002", "\u2003",
                     "\u2004", "\u2005", "\u2006", "\u2007", "\u2008",
                     "\u2009", "\u200a", "\u2028", "\u2029", "\u202f",
                     "\u205f", "\u3000"):
            return SecurityCheckResult(
                is_safe=False,
                check_id=SecurityCheckID.UNICODE_WHITESPACE,
                message=f"Unicode whitespace character at position {i}",
            )
    return SecurityCheckResult(is_safe=True)


def analyze_command_security(command: str) -> SecurityCheckResult:
    """Run all security checks on a command.

    Returns the first failing check, or a safe result if all pass.
    FAIL-CLOSED: if any check fails, the command is unsafe.
    """
    checks = [
        check_control_characters,
        check_unicode_whitespace,
        check_newlines,
        check_command_substitution,
        check_zsh_dangerous_commands,
        check_dangerous_builtins,
    ]

    for check in checks:
        result = check(command)
        if not result.is_safe:
            return result

    return SecurityCheckResult(is_safe=True)
```

#### 任务 2：输入验证 (`bash_input_validation.py`)

```python
"""Input validation for BashTool.

Mirrors validateInput() from BashTool.tsx — detects patterns
that should be handled differently (e.g., sleep polling).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ValidationResult:
    """Result of input validation."""
    is_valid: bool = True
    message: str = ""
    error_code: int = 0


# Pattern: sleep N at the beginning of a command (N >= 2)
_SLEEP_PATTERN = re.compile(r"^\s*sleep\s+(\d+)")


def detect_blocked_sleep_pattern(command: str) -> str | None:
    """Detect sleep-based polling patterns.

    Returns the matched pattern string if detected, None otherwise.
    Sleep < 2 seconds is allowed (used for rate limiting).
    """
    match = _SLEEP_PATTERN.match(command)
    if match and int(match.group(1)) >= 2:
        return f"sleep {match.group(1)}"
    return None


def validate_bash_input(command: str, *, run_in_background: bool = False) -> ValidationResult:
    """Validate BashTool input.

    Returns ValidationResult with is_valid=False if the command
    should be blocked or redirected.
    """
    if not command.strip():
        return ValidationResult(is_valid=False, message="Empty command", error_code=1)

    # Check for sleep-based polling
    sleep_pattern = detect_blocked_sleep_pattern(command)
    if sleep_pattern is not None and not run_in_background:
        return ValidationResult(
            is_valid=False,
            message=(
                f"Blocked: {sleep_pattern}. Run blocking commands in the background "
                "using run_in_background: true"
            ),
            error_code=10,
        )

    return ValidationResult(is_valid=True)
```

#### 任务 3：集成到 BashTool (`bash.py` 修改)

关键改动：
- `execute()` 入口添加输入验证
- 安全检查作为 `checkPermissions()` 的一部分调用
- 安全检查失败时返回错误而非执行命令

---

### P0-3：只读命令验证

**目标**：让 BashTool 能智能判定只读命令，减少不必要的权限弹窗。

**新建文件**：
1. `gg-bond-code/src/gg_bond_code/tools/read_only_validation.py` — 只读命令白名单验证

**修改文件**：
1. `gg-bond-code/src/gg_bond_code/tools/bash.py` — 实现 `is_read_only()` 方法
2. `gg-bond-code/src/gg_bond_code/permissions/manager.py` — Bash 只读判定集成

**详细任务**：

#### 任务 1：只读命令白名单 (`read_only_validation.py`)

```python
"""Read-only command validation for BashTool.

Mirrors readOnlyValidation.ts — determines whether a bash command
is safe to auto-allow by checking against a whitelist of known-safe
commands and their safe flags.

Key design: FAIL-CLOSED — if a command or flag is not in the whitelist,
the command is not considered read-only.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any


class FlagArgType(Enum):
    """Type of argument a flag expects."""
    NONE = "none"        # No argument (boolean flag)
    NUMBER = "number"    # Numeric argument
    STRING = "string"    # String argument
    PATH = "path"        # Path argument


@dataclass
class CommandConfig:
    """Configuration for a known-safe command."""
    safe_flags: dict[str, FlagArgType]
    regex: re.Pattern | None = None
    respects_double_dash: bool = True


# --- Command configurations ---
# Each entry defines the flags that are safe (don't modify anything).
# Flags NOT listed are considered dangerous by default.

SAFE_COMMANDS: dict[str, CommandConfig] = {
    "ls": CommandConfig(
        safe_flags={
            "-a": FlagArgType.NONE, "--all": FlagArgType.NONE,
            "-l": FlagArgType.NONE, "--long": FlagArgType.NONE,
            "-h": FlagArgType.NONE, "--human-readable": FlagArgType.NONE,
            "-R": FlagArgType.NONE, "--recursive": FlagArgType.NONE,
            "-t": FlagArgType.NONE, "--sort=time": FlagArgType.NONE,
            "-S": FlagArgType.NONE, "--sort=size": FlagArgType.NONE,
            "-1": FlagArgType.NONE,
            "--color": FlagArgType.NONE,
            "--group-directories-first": FlagArgType.NONE,
        },
    ),
    "cat": CommandConfig(
        safe_flags={
            "-n": FlagArgType.NONE, "--number": FlagArgType.NONE,
            "-b": FlagArgType.NONE, "--number-nonblank": FlagArgType.NONE,
            "-s": FlagArgType.NONE, "--squeeze-blank": FlagArgType.NONE,
            "-A": FlagArgType.NONE, "--show-all": FlagArgType.NONE,
        },
    ),
    "head": CommandConfig(
        safe_flags={
            "-n": FlagArgType.NUMBER, "--lines": FlagArgType.NUMBER,
            "-c": FlagArgType.NUMBER, "--bytes": FlagArgType.NUMBER,
        },
    ),
    "tail": CommandConfig(
        safe_flags={
            "-n": FlagArgType.NUMBER, "--lines": FlagArgType.NUMBER,
            "-c": FlagArgType.NUMBER, "--bytes": FlagArgType.NUMBER,
            "-f": FlagArgType.NONE, "--follow": FlagArgType.NONE,
            "-F": FlagArgType.NONE,
        },
    ),
    "wc": CommandConfig(
        safe_flags={
            "-l": FlagArgType.NONE, "--lines": FlagArgType.NONE,
            "-w": FlagArgType.NONE, "--words": FlagArgType.NONE,
            "-c": FlagArgType.NONE, "--bytes": FlagArgType.NONE,
            "-m": FlagArgType.NONE, "--chars": FlagArgType.NONE,
        },
    ),
    "find": CommandConfig(
        safe_flags={
            "-name": FlagArgType.STRING, "-iname": FlagArgType.STRING,
            "-type": FlagArgType.STRING, "-maxdepth": FlagArgType.NUMBER,
            "-mindepth": FlagArgType.NUMBER,
            "-path": FlagArgType.STRING, "-ipath": FlagArgType.STRING,
            "-size": FlagArgType.STRING, "-mtime": FlagArgType.STRING,
            "-newer": FlagArgType.PATH,
            "-print": FlagArgType.NONE, "-print0": FlagArgType.NONE,
            "-not": FlagArgType.NONE, "-and": FlagArgType.NONE, "-or": FlagArgType.NONE,
            # SECURITY: -exec and -delete are NOT in this list
        },
    ),
    "grep": CommandConfig(
        safe_flags={
            "-i": FlagArgType.NONE, "--ignore-case": FlagArgType.NONE,
            "-r": FlagArgType.NONE, "-R": FlagArgType.NONE,
            "--recursive": FlagArgType.NONE,
            "-n": FlagArgType.NONE, "--line-number": FlagArgType.NONE,
            "-l": FlagArgType.NONE, "--files-with-matches": FlagArgType.NONE,
            "-L": FlagArgType.NONE, "--files-without-match": FlagArgType.NONE,
            "-c": FlagArgType.NONE, "--count": FlagArgType.NONE,
            "-v": FlagArgType.NONE, "--invert-match": FlagArgType.NONE,
            "-w": FlagArgType.NONE, "--word-regexp": FlagArgType.NONE,
            "-x": FlagArgType.NONE, "--line-regexp": FlagArgType.NONE,
            "-E": FlagArgType.NONE, "--extended-regexp": FlagArgType.NONE,
            "-F": FlagArgType.NONE, "--fixed-strings": FlagArgType.NONE,
            "-e": FlagArgType.STRING, "--regexp": FlagArgType.STRING,
            "-f": FlagArgType.PATH, "--file": FlagArgType.PATH,
            "--include": FlagArgType.STRING, "--exclude": FlagArgType.STRING,
            "--color": FlagArgType.NONE,
        },
    ),
    "git": CommandConfig(
        safe_flags={
            # Read-only subcommands are safe
            "status": FlagArgType.NONE,
            "log": FlagArgType.NONE,
            "diff": FlagArgType.NONE,
            "show": FlagArgType.NONE,
            "branch": FlagArgType.NONE,  # branch -l (list)
            "tag": FlagArgType.NONE,      # tag -l (list)
            "remote": FlagArgType.NONE,    # remote -v
            "stash": FlagArgType.NONE,     # stash list/show
            "describe": FlagArgType.NONE,
            "rev-parse": FlagArgType.NONE,
            "reflog": FlagArgType.NONE,
            "shortlog": FlagArgType.NONE,
            "blame": FlagArgType.NONE,
            # Common flags
            "--oneline": FlagArgType.NONE,
            "--stat": FlagArgType.NONE,
            "--short": FlagArgType.NONE,
            "--porcelain": FlagArgType.NONE,
            "-n": FlagArgType.NUMBER,
            "--no-color": FlagArgType.NONE,
        },
    ),
    "echo": CommandConfig(
        safe_flags={
            "-n": FlagArgType.NONE, "-e": FlagArgType.NONE, "-E": FlagArgType.NONE,
        },
    ),
    "which": CommandConfig(safe_flags={}),
    "whereis": CommandConfig(safe_flags={}),
    "whoami": CommandConfig(safe_flags={}),
    "pwd": CommandConfig(safe_flags={}),
    "date": CommandConfig(safe_flags={}),
    "uname": CommandConfig(
        safe_flags={
            "-a": FlagArgType.NONE, "-r": FlagArgType.NONE,
            "-m": FlagArgType.NONE, "-s": FlagArgType.NONE,
        },
    ),
    "df": CommandConfig(
        safe_flags={
            "-h": FlagArgType.NONE, "--human-readable": FlagArgType.NONE,
            "-i": FlagArgType.NONE, "--inodes": FlagArgType.NONE,
        },
    ),
    "du": CommandConfig(
        safe_flags={
            "-h": FlagArgType.NONE, "--human-readable": FlagArgType.NONE,
            "-s": FlagArgType.NONE, "--summarize": FlagArgType.NONE,
            "-d": FlagArgType.NUMBER, "--max-depth": FlagArgType.NUMBER,
        },
    ),
    "tree": CommandConfig(
        safe_flags={
            "-L": FlagArgType.NUMBER,
            "-d": FlagArgType.NONE,
            "-a": FlagArgType.NONE,
            "-h": FlagArgType.NONE,
            "--dirsfirst": FlagArgType.NONE,
        },
    ),
    "python": CommandConfig(
        safe_flags={
            "--version": FlagArgType.NONE, "-V": FlagArgType.NONE,
            "--help": FlagArgType.NONE, "-h": FlagArgType.NONE,
            "-c": FlagArgType.STRING,
            # SECURITY: -c is borderline — it can execute arbitrary code,
            # but it's commonly used for version checks. Marked as safe
            # with the understanding that the security layer catches
            # dangerous patterns separately.
        },
    ),
    "node": CommandConfig(
        safe_flags={
            "--version": FlagArgType.NONE, "-v": FlagArgType.NONE,
            "--help": FlagArgType.NONE,
            "-e": FlagArgType.STRING,  # Same caveat as python -c
        },
    ),
    "go": CommandConfig(
        safe_flags={
            "version": FlagArgType.NONE,
            "env": FlagArgType.NONE,
            "list": FlagArgType.NONE,
        },
    ),
    "cargo": CommandConfig(
        safe_flags={
            "--version": FlagArgType.NONE, "-V": FlagArgType.NONE,
            "--help": FlagArgType.NONE, "-h": FlagArgType.NONE,
        },
    ),
    "npm": CommandConfig(
        safe_flags={
            "--version": FlagArgType.NONE, "-v": FlagArgType.NONE,
            "list": FlagArgType.NONE, "ls": FlagArgType.NONE,
            "view": FlagArgType.NONE, "info": FlagArgType.NONE,
            "outdated": FlagArgType.NONE,
        },
    ),
    "pip": CommandConfig(
        safe_flags={
            "--version": FlagArgType.NONE,
            "list": FlagArgType.NONE, "show": FlagArgType.NONE,
            "search": FlagArgType.NONE, "check": FlagArgType.NONE,
        },
    ),
    "docker": CommandConfig(
        safe_flags={
            "ps": FlagArgType.NONE, "images": FlagArgType.NONE,
            "version": FlagArgType.NONE, "info": FlagArgType.NONE,
            "logs": FlagArgType.NONE, "inspect": FlagArgType.NONE,
        },
    ),
    "curl": CommandConfig(
        safe_flags={
            "-s": FlagArgType.NONE, "--silent": FlagArgType.NONE,
            "-I": FlagArgType.NONE, "--head": FlagArgType.NONE,
            "-o": FlagArgType.PATH, "--output": FlagArgType.PATH,
            "-w": FlagArgType.STRING, "--write-out": FlagArgType.STRING,
            "-L": FlagArgType.NONE, "--location": FlagArgType.NONE,
            "-k": FlagArgType.NONE, "--insecure": FlagArgType.NONE,
            "--max-time": FlagArgType.NUMBER,
            # SECURITY: -d/--data is NOT in this list (can send POST)
        },
    ),
}
# NOTE: This is a subset of Claude Code's 100+ command configurations.
# Additional commands should be added as needed.


def _extract_base_and_args(command: str) -> tuple[str, list[str]]:
    """Extract base command and arguments from a command string.

    Handles:
    - Leading environment variables (KEY=value ...)
    - Path-prefixed commands (/usr/bin/git -> git)
    - Safe wrapper stripping (nice, timeout, time, ...)
    """
    from .bash_security import BARE_SHELL_PREFIXES

    tokens = command.strip().split()
    if not tokens:
        return "", []

    # Skip environment variable assignments
    env_var_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
    i = 0
    while i < len(tokens) and env_var_re.match(tokens[i]):
        i += 1

    if i >= len(tokens):
        return "", []

    # Strip safe wrappers
    base = tokens[i].rsplit("/", 1)[-1] if "/" in tokens[i] else tokens[i]
    while base in BARE_SHELL_PREFIXES and i + 1 < len(tokens):
        # Skip wrapper argument if present (e.g., "timeout 30")
        i += 1
        if base in ("timeout", "nice", "stdbuf", "nohup", "time"):
            # These wrappers may take a number argument
            if i < len(tokens) and tokens[i].lstrip("-").replace(".", "").isdigit():
                i += 1
        if i >= len(tokens):
            return "", []
        base = tokens[i].rsplit("/", 1)[-1] if "/" in tokens[i] else tokens[i]

    args = tokens[i + 1:]
    return base, args


def is_read_only_command(command: str) -> bool:
    """Check if a command is read-only based on the whitelist.

    FAIL-CLOSED: if the command or any of its flags are not in the
    whitelist, returns False.

    Args:
        command: The bash command to check.

    Returns:
        True if the command is considered safe/read-only.
    """
    # First, run security checks
    from .bash_security import analyze_command_security
    security_result = analyze_command_security(command)
    if not security_result.is_safe:
        return False

    # Extract base command and arguments
    base, args = _extract_base_and_args(command)
    if not base:
        return False

    # Look up command config
    config = SAFE_COMMANDS.get(base)
    if config is None:
        # Unknown command — not in whitelist -> fail-closed
        return False

    # Check all flags against safe list
    i = 0
    while i < len(args):
        arg = args[i]

        # Positional arguments (paths, patterns) are generally safe
        if not arg.startswith("-"):
            i += 1
            continue

        # Double dash separator — everything after is positional
        if arg == "--":
            break

        # Check if this flag is in the safe list
        if arg in config.safe_flags:
            flag_type = config.safe_flags[arg]
            if flag_type != FlagArgType.NONE:
                i += 1  # Skip the argument value
            i += 1
            continue

        # Combined short flags (e.g., -la)
        if len(arg) > 2 and arg[0] == "-" and arg[1] != "-":
            # Check each character
            all_safe = all(c in {f.lstrip("-") for f in config.safe_flags if len(f) == 2}
                          for c in arg[1:] if c.isalpha())
            if all_safe:
                i += 1
                continue

        # Unknown flag — fail-closed
        return False

    return True


def check_read_only_constraints(command: str) -> bool:
    """Check if a full command (possibly with pipes/&&) is read-only.

    All sub-commands must be individually read-only for the whole
    command to be considered read-only.
    """
    # Split on pipe, && and ||
    parts = re.split(r"\s*(?:\|{1,2}|&&)\s*", command)

    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not is_read_only_command(part):
            return False

    return True
```

#### 任务 2：集成到 BashTool

- `BashTool.is_read_only()` 调用 `check_read_only_constraints()`
- `PermissionManager.check()` 已有 `is_read_only()` 自动放行逻辑，无需修改

---

### P1-1：智能权限规则

**目标**：提升权限管理体验，支持前缀规则建议和环境变量剥离。

**新建文件**：
1. `gg-bond-code/src/gg_bond_code/tools/bash_rule_suggestion.py` — 智能规则建议

**修改文件**：
1. `gg-bond-code/src/gg_bond_code/permissions/manager.py` — 集成规则建议 + 前缀匹配

**详细任务**：

#### 任务 1：智能规则建议 (`bash_rule_suggestion.py`)

```python
"""Smart permission rule suggestion for BashTool.

Mirrors getSimpleCommandPrefix() and suggestionForExactCommand()
from bashPermissions.ts — when the user approves a command,
automatically suggest a reusable permission rule.
"""

from __future__ import annotations

import re
from typing import Any


# Safe environment variables that can be stripped from command prefix
SAFE_ENV_VARS: set[str] = {
    "GOEXPERIMENT", "GOOS", "GOARCH", "CGO_ENABLED", "GO111MODULE",
    "RUST_BACKTRACE", "RUST_LOG",
    "NODE_ENV",
    "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
    # SECURITY: PATH, LD_PRELOAD, LD_LIBRARY_PATH, DYLD_*, PYTHONPATH,
    # NODE_PATH, GOFLAGS, RUSTFLAGS, NODE_OPTIONS are NOT safe
    # — they can hijack binary execution or inject code.
}

# Prefixes that should never be suggested as rule prefixes
NEVER_SUGGEST_PREFIXES: set[str] = {
    "sh", "bash", "zsh", "fish", "csh", "ksh", "dash",
    "env", "xargs", "sudo", "doas", "pkexec",
    "eval", "exec", "source",
}

# Regex for a "subcommand-like" token: lowercase alphanumeric + hyphens
_SUBCMD_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

# Regex for environment variable assignment
_ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def get_command_prefix(command: str) -> str | None:
    """Extract a reusable prefix from a command for permission rule suggestion.

    Examples:
        "git commit -m 'fix'" -> "git commit"
        "NODE_ENV=test npm run build" -> "npm run"
        "MY_VAR=val npm run build" -> None (MY_VAR not in safe vars)
        "sudo rm -rf /" -> None (sudo is never-suggest)

    Returns:
        The suggested prefix string, or None if no good prefix can be extracted.
    """
    tokens = command.strip().split()
    if not tokens:
        return None

    # Skip safe environment variable assignments
    i = 0
    while i < len(tokens) and _ENV_VAR_RE.match(tokens[i]):
        var_name = tokens[i].split("=")[0]
        if var_name not in SAFE_ENV_VARS:
            return None  # Non-safe env var -> don't suggest prefix
        i += 1

    if i >= len(tokens):
        return None

    remaining = tokens[i:]

    # Get base command
    base = remaining[0].rsplit("/", 1)[-1] if "/" in remaining[0] else remaining[0]

    # Never suggest for bare shells and dangerous prefixes
    if base in NEVER_SUGGEST_PREFIXES:
        return None

    # Need at least 2 tokens (command + subcommand)
    if len(remaining) < 2:
        return None

    # Second token must look like a subcommand
    subcmd = remaining[1]
    if not _SUBCMD_RE.match(subcmd):
        return None

    return " ".join(remaining[:2])
```

#### 任务 2：增强 PermissionManager

- `_make_key()` 中为 Bash 命令剥离安全环境变量
- `grant_session()` 中调用 `get_command_prefix()` 生成前缀规则
- 添加 `ask` 规则支持

---

### P1-2：执行引擎增强

**目标**：支持进度输出、前后台切换、大输出持久化。

**新建文件**：
1. `gg-bond-code/src/gg_bond_code/tools/bash_executor.py` — 增强的命令执行器

**修改文件**：
1. `gg-bond-code/src/gg_bond_code/tools/bash.py` — 使用新执行器

**详细任务**：

#### 任务 1：增强执行器 (`bash_executor.py`)

关键特性：
- AsyncGenerator 驱动的执行（类似 `runShellCommand`）
- 2 秒进度阈值
- `run_in_background` 支持
- 大输出文件持久化
- 退出码语义解释

```python
"""Enhanced bash command executor with progress and background support.

Mirrors runShellCommand() from BashTool.tsx — AsyncGenerator-based
execution with progress output, background task conversion, and
large output persistence.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator

from .bash_exit_codes import interpret_exit_code, ExitCodeSemantic
from .bash_semantics import is_silent_command, CommandSemantic, classify_pipeline


# --- Constants (mirrors BashTool.tsx) ---

PROGRESS_THRESHOLD_MS = 2000  # Show progress after 2 seconds
MAX_PERSISTED_SIZE = 64 * 1024 * 1024  # 64MB output limit
DEFAULT_TIMEOUT_MS = 120_000  # 2 minute default


@dataclass
class ExecResult:
    """Result from command execution."""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    is_error: bool = False
    semantic_message: str | None = None  # Exit code semantic message
    output_file_path: str | None = None  # If output was persisted to disk
    is_silent: bool = False  # Command typically produces no stdout
    semantic: CommandSemantic = CommandSemantic.UNKNOWN


@dataclass
class ProgressUpdate:
    """Progress update during command execution."""
    output: str = ""
    elapsed_seconds: float = 0.0
    total_lines: int = 0


async def run_shell_command(
    command: str,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    run_in_background: bool = False,
    working_dir: str | None = None,
) -> AsyncGenerator[ProgressUpdate, None] | ExecResult:
    """Execute a shell command with progress reporting.

    This is an AsyncGenerator that yields ProgressUpdate objects
    during execution and returns an ExecResult when complete.

    Usage:
        gen = run_shell_command("npm run build")
        result = None
        async for progress in gen:
            # Handle progress updates
            print(f"Progress: {progress.total_lines} lines")
        # After iteration completes, get the final result
        # Note: In Python, the return value of an async generator
        # is accessed differently than in TypeScript.
    """
    # ... (implementation follows the BashTool.tsx pattern)
```

---

### P2-1：沙箱执行

**目标**：实现命令运行时的隔离，限制文件系统访问和网络连接。

**新建文件**：
1. `gg-bond-code/src/gg_bond_code/tools/sandbox.py` — 沙箱管理器
2. `gg-bond-code/src/gg_bond_code/tools/sandbox_config.py` — 沙箱配置

**修改文件**：
1. `gg-bond-code/src/gg_bond_code/tools/bash.py` — 集成沙箱

**实现方案**：
- 使用 Python `subprocess` 的 `preexec_fn` 设置 `chroot` 或使用 `namespace` 隔离（Linux）
- macOS 使用 `sandbox-exec` 命令
- 配置驱动：文件系统读写路径 + 网络白名单
- 沙箱排除命令迭代剥离（不动点算法）

---

### P2-2：sed 编辑特殊处理

**目标**：将 `sed -i` 命令转换为文件编辑预览模式。

**新建文件**：
1. `gg-bond-code/src/gg_bond_code/tools/sed_parser.py` — sed 命令解析器

**修改文件**：
1. `gg-bond-code/src/gg_bond_code/tools/bash.py` — sed 编辑预览

**实现方案**：
- 解析 `sed -i 's/old/new/g' file.txt` 为 `SedEditInfo`
- 在权限确认时显示 diff 预览
- 确认后直接写入预览内容（不执行 sed），避免 TOCTOU

---

## 可迁移的设计模式

### 模式 1：纵深防御架构

- **类别**：结构模式
- **描述**：将安全检查分为多个独立维度，在权限主链路中按优先级编排。任何维度可独立阻止危险操作，同时 AST 不可用时可回退到正则路径。
- **适用场景**：任何允许用户/AI 执行动态操作的系统
- **Python 实现要点**：
  ```python
  # 每个检查是独立函数，返回 SecurityCheckResult
  # analyze_command_security 按优先级依次调用
  checks = [check_control_chars, check_unicode, check_newlines, ...]
  for check in checks:
      result = check(command)
      if not result.is_safe:
          return result
  ```

### 模式 2：Fail-Closed 白名单

- **类别**：行为模式
- **描述**：使用明确白名单定义安全行为，未知结构默认拒绝。黑名单永远不完整，白名单则有有限的已知安全项。
- **适用场景**：安全敏感的解析和验证场景
- **Python 实现要点**：
  ```python
  # is_read_only_command() 对未知命令返回 False
  # 安全标志白名单中未列出的 flag 一律视为危险
  config = SAFE_COMMANDS.get(base)
  if config is None:
      return False  # Unknown command -> fail-closed
  ```

### 模式 3：语义分类驱动的差异化处理

- **类别**：行为模式
- **描述**：对输入进行语义分类，根据分类结果采取不同的 UI 展示和安全策略。
- **适用场景**：处理多种类型输入的工具系统
- **Python 实现要点**：
  ```python
  semantic = classify_pipeline(command)
  if semantic in (CommandSemantic.SEARCH, CommandSemantic.READ, CommandSemantic.LIST):
      # 折叠显示，自动放行（如果通过只读验证）
      display_mode = "collapsible"
  elif semantic == CommandSemantic.SILENT:
      # 显示 "Done" 而非 "(No output)"
      display_mode = "done"
  ```

### 模式 4：退出码语义解释

- **类别**：行为模式
- **描述**：不同命令的非零退出码有不同含义。grep 返回 1 是"没找到"而非"出错"。
- **适用场景**：命令行工具集成、CI/CD 管道
- **Python 实现要点**：
  ```python
  semantic = interpret_exit_code(command, exit_code)
  if not semantic.is_error:
      # 非 0 退出码但不是错误（如 grep 无匹配）
      return ToolResult(output=output, error=False)
  ```

---

## 风险评估

### 架构风险
| 风险 | 影响 | 缓解策略 |
|------|------|---------|
| 安全检查绕过 — Python 正则不如 tree-sitter AST 精确 | 高 | 采用 fail-closed 设计：任何不确定的命令都需要确认 |
| 权限规则误匹配 — 前缀规则可能过于宽松 | 中 | 环境变量剥离白名单 + NEVER_SUGGEST_PREFIXES + deny 优先 |
| 只读白名单维护成本 — 新版本命令可能增加危险 flag | 中 | 文档化每个白名单决策，添加 SECURITY 注释 |

### 性能风险
| 风险 | 影响 | 缓解策略 |
|------|------|---------|
| 复合命令安全检查耗时 | 低 | 设置 MAX_SUBCOMMANDS_FOR_SECURITY_CHECK 上限 |
| 大输出内存占用 | 中 | 文件模式输出 + maxResultSizeChars 限制 |

### 向后兼容风险
| 风险 | 影响 | 缓解策略 |
|------|------|---------|
| `is_read_only()` 从 False 变为条件性 True | 低 | 只读判定只会增加放行，不会拒绝以前允许的命令 |
| 退出码语义改变错误判定 | 中 | 添加 `is_error` 标志同时保留 `error` 字段兼容 |

---

## 测试与验证计划

### 单元测试

#### `tests/unit/test_bash_semantics.py`
- `test_classify_search_commands` — find/grep/rg 归类为 SEARCH
- `test_classify_read_commands` — cat/head/tail 归类为 READ
- `test_classify_list_commands` — ls/tree/du 归类为 LIST
- `test_classify_silent_commands` — mv/cp/rm 归类为 SILENT
- `test_classify_pipeline_all_read` — `ls dir && echo "---" && ls dir2` 归类为 LIST
- `test_classify_pipeline_mixed` — `ls dir && rm file` 归类为 DESTRUCTIVE
- `test_is_silent_command` — mv/cp 返回 True

#### `tests/unit/test_bash_exit_codes.py`
- `test_grep_no_matches` — exit 1 -> not error, message "No matches found"
- `test_grep_error` — exit 2 -> error
- `test_diff_differ` — exit 1 -> not error, message "Files differ"
- `test_default_nonzero` — exit 1 for unknown -> error

#### `tests/unit/test_bash_security.py`
- `test_command_substitution` — 检测 `$(...)` 等
- `test_zsh_dangerous_commands` — 检测 zmodload/emulate 等
- `test_dangerous_builtins` — 检测 eval/exec/source
- `test_newlines` — 检测换行符注入
- `test_control_characters` — 检测控制字符
- `test_unicode_whitespace` — 检测 Unicode 空白
- `test_safe_commands_pass` — `ls -la` / `git status` 通过
- `test_analyze_command_security_safe` — 综合安全通过
- `test_analyze_command_security_unsafe` — 综合安全失败

#### `tests/unit/test_read_only_validation.py`
- `test_ls_read_only` — `ls -la /tmp` 只读
- `test_ls_with_unknown_flag` — `ls --evil` 非只读
- `test_git_status_read_only` — `git status` 只读
- `test_git_commit_not_read_only` — `git commit` 非只读
- `test_find_read_only` — `find . -name "*.py"` 只读
- `test_find_exec_not_read_only` — `find . -exec rm {} \;` 非只读
- `test_unknown_command_not_read_only` — 未知命令 fail-closed
- `test_safe_env_var_stripped` — `NODE_ENV=test npm run build` 正确识别
- `test_unsafe_env_var_rejected` — `PATH=evil npm run build` 非只读
- `test_pipeline_all_read_only` — 管道中全只读命令
- `test_pipeline_mixed` — 管道中有非只读命令

#### `tests/unit/test_bash_rule_suggestion.py`
- `test_git_commit_prefix` — `git commit -m "fix"` -> `git commit`
- `test_npm_run_prefix` — `npm run build` -> `npm run`
- `test_safe_env_var_prefix` — `NODE_ENV=test npm run build` -> `npm run`
- `test_unsafe_env_var_no_prefix` — `PATH=evil npm run build` -> None
- `test_sudo_no_prefix` — `sudo rm -rf /` -> None
- `test_bash_no_prefix` — `bash -c 'echo hi'` -> None
- `test_single_command_no_prefix` — `ls` -> None

### 集成测试
- `test_bash_tool_is_read_only_integration` — BashTool.is_read_only() 使用只读验证
- `test_permission_manager_bash_read_only` — 只读 Bash 命令自动放行
- `test_permission_manager_bash_dangerous` — 危险 Bash 命令仍需确认

### 验证清单
- [ ] `ls -la` 不弹出权限确认（只读自动放行）
- [ ] `git status` 不弹出权限确认
- [ ] `git commit` 仍需权限确认
- [ ] `rm -rf /` 仍需权限确认
- [ ] `eval $(echo evil)` 被安全检查拦截
- [ ] `grep` 无匹配返回 1 时显示 "No matches found" 而非错误
- [ ] `NODE_ENV=test npm run build` 的规则建议为 `npm run`
- [ ] `PATH=evil npm run build` 不生成规则建议
